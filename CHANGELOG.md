# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Hugging Face Hub integration (`hf_utils.py`) for uploading experiment results
- Reproducible benchmark suite in `experiments/`
- Research paper templates in `paper/`
- GitHub Actions workflows for CI/CD and PyPI publishing
- Comprehensive CONTRIBUTING.md guide

### Changed
- Enhanced `pyproject.toml` with full metadata and tool configurations
- Improved `__init__.py` with better docstrings and exports
- Updated adapters `__init__.py` with lazy imports

## [0.1.0] - 2024-XX-XX

### Added
- Initial release
- Core `MessageBus` class with async context manager support
- `Message` model with Pydantic validation
- `BrokerAdapter` abstract base class
- `InMemoryBroker` for testing and single-process use
- `RedisBroker` adapter for production deployments
- `RabbitMQBroker` adapter
- `KafkaBroker` adapter
- Communication patterns:
  - Fire-and-forget (default)
  - Wait for acknowledgment
  - Request-response
- Full type hints throughout
- Google-style docstrings
- pytest test suite

### Dependencies
- Core: `pydantic>=2.0.0`, `anyio>=3.0.0`
- Redis: `redis>=4.0.0`
- RabbitMQ: `aio-pika>=9.0.0`
- Kafka: `aiokafka>=0.8.0`

[Unreleased]: https://github.com/imran-siddique/amb/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/imran-siddique/amb/releases/tag/v0.1.0
