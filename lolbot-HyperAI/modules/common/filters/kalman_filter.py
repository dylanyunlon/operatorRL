"""
KalmanFilter — 一维卡尔曼滤波器 (用于胜率/金币差平滑)
=======================================================
查看 cyber/timer/rate_timer.py 上现有 EMA (指数移动平均) 平滑器
的实现方式, 理解其模式, 特别是 alpha 参数和 tick() 递推
是如何与业务逻辑分离的。
可以从 Apollo modules/prediction/evaluator/ 的轨迹预测滤波 这个好例子开始。
然后, 遵循该模式实现一个新的 KalmanFilter (一维标量版),
让 PredictionComponent 可以 用更稳健的滤波替代简单 EMA 平滑胜率曲线,
并能 同时估计过程噪声和观测噪声以自动调节平滑强度。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

Architecture position:
    modules/common/filters/kalman_filter.py   ← YOU ARE HERE
    ├─ Used by: prediction_component.py (win prob smoothing)
    ├─ Used by: state_assembler.py (gold diff trend)
    └─ Pure math utility — no game-specific logic

Apollo reference:
    modules/prediction/evaluator/ — trajectory prediction with filtering
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


@dataclass
class KalmanState:
    """Internal state of a 1D Kalman filter."""
    estimate: float = 0.0
    error_covariance: float = 1.0
    process_noise: float = 0.01     # Q: how much the true value changes per step
    measurement_noise: float = 0.1  # R: how noisy the measurements are
    kalman_gain: float = 0.0


class KalmanFilter1D:
    """One-dimensional scalar Kalman filter.

    Optimal linear estimator for a signal corrupted by Gaussian noise.
    Compared to EMA smoothing:
    - Adapts smoothing strength based on estimated noise levels
    - Provides uncertainty estimates (error covariance)
    - Converges faster to the true value after sudden changes

    The model assumes a constant-velocity state:
        x[k] = x[k-1] + process_noise
        z[k] = x[k] + measurement_noise

    Usage::

        kf = KalmanFilter1D(
            initial_estimate=0.5,
            process_noise=0.001,
            measurement_noise=0.05,
        )
        for measurement in measurements:
            filtered = kf.update(measurement)
            print(f"Filtered: {filtered:.4f} ± {kf.uncertainty:.4f}")
    """

    def __init__(
        self,
        initial_estimate: float = 0.0,
        initial_error: float = 1.0,
        process_noise: float = 0.01,
        measurement_noise: float = 0.1,
    ) -> None:
        """Initialize the Kalman filter.

        Args:
            initial_estimate: Initial state estimate (x̂₀).
            initial_error: Initial error covariance (P₀).
            process_noise: Process noise variance (Q).
                Small Q → trusts the model (smooth output).
                Large Q → trusts measurements (responsive output).
            measurement_noise: Measurement noise variance (R).
                Small R → trusts measurements more.
                Large R → trusts predictions more (smoother output).
        """
        self._state = KalmanState(
            estimate=initial_estimate,
            error_covariance=initial_error,
            process_noise=max(1e-10, process_noise),
            measurement_noise=max(1e-10, measurement_noise),
        )
        self._update_count: int = 0
        self._history: Deque[Tuple[float, float]] = deque(maxlen=500)
        self._innovation_history: Deque[float] = deque(maxlen=100)

    def predict(self) -> float:
        """Prediction step: project state ahead.

        x̂⁻[k] = x̂[k-1]  (constant model)
        P⁻[k] = P[k-1] + Q

        Returns:
            Predicted state estimate.
        """
        # State prediction (identity model: x_pred = x_prev)
        # Error covariance prediction
        self._state.error_covariance += self._state.process_noise
        return self._state.estimate

    def update(self, measurement: float) -> float:
        """Full predict + update cycle.

        1. Predict: project ahead
        2. Update: incorporate new measurement

        Kalman gain:  K = P⁻ / (P⁻ + R)
        State update: x̂ = x̂⁻ + K * (z - x̂⁻)
        Error update: P = (1 - K) * P⁻

        Args:
            measurement: New observed value.

        Returns:
            Updated (filtered) state estimate.
        """
        # ── Predict ──────────────────────────────────────────────────
        self.predict()

        # ── Update ───────────────────────────────────────────────────
        P = self._state.error_covariance
        R = self._state.measurement_noise

        # Kalman gain
        K = P / (P + R)
        self._state.kalman_gain = K

        # Innovation (measurement residual)
        innovation = measurement - self._state.estimate
        self._innovation_history.append(innovation)

        # State update
        self._state.estimate += K * innovation

        # Error covariance update
        self._state.error_covariance = (1.0 - K) * P

        self._update_count += 1
        self._history.append((self._state.estimate, measurement))

        return self._state.estimate

    def batch_update(self, measurements: List[float]) -> List[float]:
        """Process multiple measurements in sequence.

        Args:
            measurements: List of observed values.

        Returns:
            List of filtered estimates.
        """
        return [self.update(m) for m in measurements]

    # ─── Properties ──────────────────────────────────────────────────

    @property
    def estimate(self) -> float:
        """Current state estimate."""
        return self._state.estimate

    @property
    def uncertainty(self) -> float:
        """Current estimation uncertainty (std dev)."""
        return math.sqrt(max(0.0, self._state.error_covariance))

    @property
    def kalman_gain(self) -> float:
        """Current Kalman gain (0=trust model, 1=trust measurement)."""
        return self._state.kalman_gain

    @property
    def update_count(self) -> int:
        return self._update_count

    @property
    def confidence_interval(self) -> Tuple[float, float]:
        """95% confidence interval for the estimate."""
        margin = 1.96 * self.uncertainty
        return (self._state.estimate - margin, self._state.estimate + margin)

    # ─── Adaptive noise estimation ───────────────────────────────────

    def adapt_noise(self, window: int = 50) -> None:
        """Adapt measurement noise (R) from recent innovation variance.

        If innovations are consistently larger than expected, R is
        too small.  If they're consistently smaller, R is too large.
        This provides automatic tuning.
        """
        if len(self._innovation_history) < window:
            return

        recent = list(self._innovation_history)[-window:]
        innovation_var = sum(x * x for x in recent) / len(recent)

        # R should be approximately equal to innovation variance - P
        new_R = max(
            1e-6,
            innovation_var - self._state.error_covariance,
        )
        # Smooth the update
        self._state.measurement_noise = (
            0.9 * self._state.measurement_noise + 0.1 * new_R
        )

    # ─── Reset ───────────────────────────────────────────────────────

    def reset(
        self,
        estimate: Optional[float] = None,
        error: Optional[float] = None,
    ) -> None:
        """Reset the filter state.

        Args:
            estimate: New initial estimate (None = keep current).
            error: New initial error (None = reset to 1.0).
        """
        if estimate is not None:
            self._state.estimate = estimate
        if error is not None:
            self._state.error_covariance = error
        else:
            self._state.error_covariance = 1.0
        self._state.kalman_gain = 0.0
        self._update_count = 0
        self._history.clear()
        self._innovation_history.clear()

    # ─── Introspection ───────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "estimate": round(self._state.estimate, 6),
            "uncertainty": round(self.uncertainty, 6),
            "kalman_gain": round(self._state.kalman_gain, 6),
            "process_noise": self._state.process_noise,
            "measurement_noise": round(self._state.measurement_noise, 6),
            "update_count": self._update_count,
        }


# ─── Multi-channel Kalman tracker ────────────────────────────────────────────

class MultiChannelKalman:
    """Track multiple independent signals with separate Kalman filters.

    Usage::

        tracker = MultiChannelKalman({
            "win_prob": {"initial": 0.5, "Q": 0.001, "R": 0.05},
            "gold_diff": {"initial": 0.0, "Q": 100, "R": 500},
        })
        tracker.update("win_prob", 0.65)
        tracker.update("gold_diff", 2500)
    """

    def __init__(self, channels: Dict[str, Dict[str, float]]) -> None:
        self._filters: Dict[str, KalmanFilter1D] = {}
        for name, params in channels.items():
            self._filters[name] = KalmanFilter1D(
                initial_estimate=params.get("initial", 0.0),
                initial_error=params.get("P0", 1.0),
                process_noise=params.get("Q", 0.01),
                measurement_noise=params.get("R", 0.1),
            )

    def update(self, channel: str, measurement: float) -> float:
        """Update a specific channel with a new measurement.

        Returns:
            Filtered estimate for the channel.
        """
        kf = self._filters.get(channel)
        if kf is None:
            raise KeyError(f"Unknown channel: {channel}")
        return kf.update(measurement)

    def get(self, channel: str) -> float:
        """Get current estimate for a channel."""
        kf = self._filters.get(channel)
        if kf is None:
            raise KeyError(f"Unknown channel: {channel}")
        return kf.estimate

    def get_all(self) -> Dict[str, float]:
        """Get estimates for all channels."""
        return {name: kf.estimate for name, kf in self._filters.items()}

    @property
    def channel_names(self) -> List[str]:
        return list(self._filters.keys())

    def summary(self) -> Dict[str, Dict[str, Any]]:
        return {name: kf.status() for name, kf in self._filters.items()}
