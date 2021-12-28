from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in rarison_motors/__init__.py
from rarison_motors import __version__ as version

setup(
	name="rarison_motors",
	version=version,
	description="App for Rarison Motors sales",
	author="MM0",
	author_email="neerajvkn@gmail.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
