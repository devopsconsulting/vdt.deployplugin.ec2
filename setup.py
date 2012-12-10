# coding: utf-8
"""
avira.deployplugin.ec2
=============================

THe EC2 provider for the avira.deploy tool.
"""

from setuptools import setup
from setuptools import find_packages

version = '0.0.1'

setup(
    name='avira.deployplugin.ec2',
    version=version,
    description="Avira Deployment Tool EC2 provider",
    long_description=__doc__,
    classifiers=[],
    # Get strings from
    #http://pypi.python.org/pypi?%3Aaction=list_classifiers
    keywords='',
    author='Cosmin Luță',
    author_email='cosmin.luta@avira.com',
    url='https://github.dtc.avira.com/VDT/avira.deployplugin.ec2',
    license='Avira VDT 2012',
    packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
    namespace_packages=['avira', 'avira.deployplugin'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'distribute',
        'boto',
        'straight.plugin',
        'avira.deploy',
        # -*- Extra requirements: -*-
    ],
    entry_points={}
)
