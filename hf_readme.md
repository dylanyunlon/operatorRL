---
license: mit
task_categories:
  - text-generation
language:
  - en
tags:
  - code-generation
  - verification
  - benchmarks
  - humaneval
pretty_name: CMVK Benchmarks
size_categories:
  - n<1K
---

# CMVK Benchmarks

Benchmark datasets for the **Cross-Model Verification Kernel (CMVK)** research project.

## Datasets

| File | Description | Size |
|------|-------------|------|
| `humaneval_50.json` | First 50 HumanEval problems | 50 problems |
| `humaneval_full.json` | Complete HumanEval benchmark | 164 problems |
| `humaneval_sample.json` | Small sample for testing | 10 problems |
| `sabotage.json` | Red-team test cases (correct + buggy) | 40 cases |
| `sample.json` | General test samples | Various |

## Usage

```python
from datasets import load_dataset

# Load specific file
dataset = load_dataset("imran-siddique/cmvk-benchmarks", data_files="datasets/humaneval_50.json")

# Or download directly
from huggingface_hub import hf_hub_download
path = hf_hub_download(repo_id="imran-siddique/cmvk-benchmarks", filename="datasets/sabotage.json", repo_type="dataset")
```

## Paper

See our paper: [Cross-Model Verification Kernel](https://github.com/imran-siddique/cross-model-verification-kernel)

## Citation

```bibtex
@misc{cmvk2026,
  title={Cross-Model Verification Kernel: Adversarial Multi-Model Code Generation},
  author={Siddique, Imran},
  year={2026},
  url={https://github.com/imran-siddique/cross-model-verification-kernel}
}
```

## License

MIT License
