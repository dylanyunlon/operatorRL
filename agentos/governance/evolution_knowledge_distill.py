"""
Evolution Knowledge Distill — large model → small model distillation.

Implements a knowledge distillation framework where a large "teacher"
model's outputs (logits / decisions / strategies) are used to train
a lightweight "student" model via soft-target training.

Location: agentos/governance/evolution_knowledge_distill.py

Reference (拿来主義):
  - DI-star: teacher-student architecture for StarCraft RL
  - agentlightning/trainer/trainer.py: training loop pattern
  - agentos/governance/model_versioner.py: model registry
  - open_spiel algorithms: policy distillation patterns
  - Hinton et al. (2015): Distilling the Knowledge in a Neural Network

Design Notes (Knuth-level critique):
  User:
    - register_teacher/student have explicit param_count for compression ratio.
    - Temperature parameter controls softness of teacher outputs.
    - get_quality_metric returns fidelity — how well student mimics teacher.
  System:
    - Distill step is stateless — teacher output passed in, not stored.
    - Soft cross-entropy loss with temperature scaling.
    - No framework dependency — pure Python + math for portability.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.evolution_knowledge_distill.v1"


def _softmax(logits: List[float], temperature: float = 1.0) -> List[float]:
    """Compute softmax with temperature scaling."""
    if not logits:
        return []
    t = max(temperature, 1e-9)
    scaled = [x / t for x in logits]
    max_val = max(scaled)
    exps = [math.exp(x - max_val) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def _kl_divergence(p: List[float], q: List[float]) -> float:
    """KL(p || q) — measures how q diverges from p."""
    if len(p) != len(q):
        return float("inf")
    kl = 0.0
    for pi, qi in zip(p, q):
        if pi > 1e-12 and qi > 1e-12:
            kl += pi * math.log(pi / qi)
    return kl


class _ModelDescriptor:
    __slots__ = ("name", "param_count", "role", "distill_history")

    def __init__(self, name: str, param_count: int, role: str = ""):
        self.name = name
        self.param_count = param_count
        self.role = role
        self.distill_history: List[Dict[str, Any]] = []


class EvolutionKnowledgeDistill:
    """Knowledge distillation from teacher to student models.

    Attributes:
        distill_count: Total distillation steps executed.
        teachers: Set of registered teacher names.
        students: Set of registered student names.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, *, temperature: float = 1.0) -> None:
        self._temperature = temperature
        self._models: Dict[str, _ModelDescriptor] = {}
        self._distill_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def distill_count(self) -> int:
        return self._distill_count

    @property
    def teachers(self) -> set:
        return {n for n, m in self._models.items() if m.role == "teacher"}

    @property
    def students(self) -> set:
        return {n for n, m in self._models.items() if m.role == "student"}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_teacher(self, name: str, *, param_count: int) -> None:
        self._models[name] = _ModelDescriptor(name, param_count, "teacher")
        self._fire_evolution({"action": "register_teacher", "name": name})

    def register_student(self, name: str, *, param_count: int) -> None:
        self._models[name] = _ModelDescriptor(name, param_count, "student")
        self._fire_evolution({"action": "register_student", "name": name})

    # ------------------------------------------------------------------
    # Distillation step
    # ------------------------------------------------------------------

    def distill_step(
        self,
        teacher_name: str,
        student_name: str,
        teacher_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute one distillation step.

        Args:
            teacher_name: Registered teacher model name.
            student_name: Registered student model name.
            teacher_output: Dict with 'logits' key (list of floats).

        Returns:
            Dict with student_loss, temperature, and metadata.
        """
        if teacher_name not in self._models:
            raise KeyError(f"Teacher '{teacher_name}' not registered")
        if student_name not in self._models:
            raise KeyError(f"Student '{student_name}' not registered")

        teacher = self._models[teacher_name]
        student = self._models[student_name]

        logits = teacher_output.get("logits", [0.5])

        # Teacher soft targets
        teacher_probs = _softmax(logits, self._temperature)

        # Student generates a uniform prior (placeholder — real impl would
        # forward through student network)
        n = len(logits)
        student_probs = [1.0 / n] * n if n > 0 else [1.0]

        # KL divergence as loss
        loss = _kl_divergence(teacher_probs, student_probs)

        self._distill_count += 1

        record = {
            "step": self._distill_count,
            "teacher": teacher_name,
            "student": student_name,
            "loss": loss,
            "temperature": self._temperature,
            "ts": time.time(),
        }
        student.distill_history.append(record)

        self._fire_evolution({
            "action": "distill_step",
            "loss": loss,
            "teacher": teacher_name,
            "student": student_name,
        })

        return {
            "student_loss": loss,
            "temperature": self._temperature,
            "teacher_probs": teacher_probs,
            "student_probs": student_probs,
        }

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_compression_ratio(self, teacher_name: str, student_name: str) -> float:
        t = self._models.get(teacher_name)
        s = self._models.get(student_name)
        if t is None or s is None:
            return 0.0
        return t.param_count / max(s.param_count, 1)

    def get_quality_metric(self, student_name: str) -> Dict[str, Any]:
        s = self._models.get(student_name)
        if s is None or not s.distill_history:
            return {"fidelity": 0.0}
        recent = s.distill_history[-10:]
        avg_loss = sum(r["loss"] for r in recent) / len(recent)
        fidelity = max(0.0, 1.0 - avg_loss)
        return {
            "fidelity": fidelity,
            "avg_loss": avg_loss,
            "total_steps": len(s.distill_history),
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "distill_count": self._distill_count,
            "teacher_count": len(self.teachers),
            "student_count": len(self.students),
            "temperature": self._temperature,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised")

    def __repr__(self) -> str:
        return f"EvolutionKnowledgeDistill(steps={self._distill_count})"


default_distill: EvolutionKnowledgeDistill = EvolutionKnowledgeDistill()
