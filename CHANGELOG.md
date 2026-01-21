# Changelog

All notable changes to the Cross-Model Verification Kernel (CMVK) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Anthropic Claude verifier support (`AnthropicVerifier`)
- CLI interface with `cmvk` command
  - `cmvk run` - Run verification on a task
  - `cmvk config` - Manage configuration
  - `cmvk benchmark` - Run benchmark experiments
  - `cmvk visualize` - Visualize trace files
  - `cmvk models` - List available models
- Reproducibility controls with seed configuration
- `pyproject.toml` for modern Python packaging
- GitHub Actions CI/CD pipeline
- Docker support with improved documentation
- Per-agent temperature controls
- Benchmark results table in README

### Changed
- Pinned all dependency versions in `requirements.txt`
- Updated README with installation via pip, CLI usage, and Docker instructions
- Improved configuration handling with seed support

### Fixed
- N/A

## [1.0.0] - 2024-01-21

### Added
- Initial release of Cross-Model Verification Kernel
- OpenAI Generator agent (GPT-4o, GPT-4 Turbo, o1 models)
- Gemini Verifier agent (Gemini 1.5 Pro/Flash)
- Graph of Truth state machine for loop prevention
- Prosecutor Mode for hostile test generation
- Trace logging and visualization system
- HumanEval dataset integration
- Lateral thinking via strategy banning
- Sandbox code execution
- Configuration via YAML files
- Basic test suite
- Research paper draft (PAPER.md)

### Security
- Basic sandbox isolation for code execution
- API key handling via environment variables

---

## Version History

- **1.0.0**: Initial research release with OpenAI + Gemini support
- **1.1.0** (upcoming): Anthropic support, CLI, improved reproducibility
