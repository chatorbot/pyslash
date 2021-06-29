import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="pyslash",
    version="0.0.1",
    author="Chator Dev Team",
    author_email="devs@chator.ai",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/chatorbot/pyslash",
    project_urls={
        "Bug Tracker": "https://github.com/chatorbot/pyslash/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "pyslash"},
    packages=setuptools.find_packages(where="pyslash"),
    python_requires=">=3.6",
)