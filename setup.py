#!/usr/bin/env python3
"""Setup script for pre-commit-tidy."""

from setuptools import setup

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="pre-commit-tidy",
    version="1.0.0",
    description="Pre-commit hook for automated file organization",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="codefuturist",
    url="https://github.com/codefuturist/pre-commit-tidy",
    license="MIT",
    py_modules=["tidy"],
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "tidy=tidy:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Quality Assurance",
    ],
    keywords="pre-commit hook file organization tidy",
)
