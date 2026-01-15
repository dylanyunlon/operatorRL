from setuptools import setup, find_packages

setup(
    name="self-correcting-agent-kernel",
    version="0.1.0",
    description="Self-correcting agent kernel that analyzes failures and patches agents",
    author="Self-Correcting Agent Team",
    packages=find_packages(),
    install_requires=[
        "pydantic>=2.0.0",
        "pyyaml>=6.0",
        "requests>=2.31.0",
    ],
    python_requires=">=3.8",
)
