# MCUXpresso SDK
The NXP MCUXpresso software and tools offer comprehensive development solutions designed to help accelerate embedded system development of applications based on MCUs from NXP. The MCUXpresso SDK includes a flexible set of peripheral drivers designed to speed up and simplify development of embedded applications. Along with the peripheral drivers, the MCUXpresso SDK provides an extensive set of example applications covering everything
from basic peripheral use cases to full technology demonstrations. The MCUXpresso SDK contains optional RTOS integrations such as FreeRTOS, and various other middleware to support rapid development.

The MCUXpresso SDK on GitHub is composed of multiple groups of software distributed among different repositories. The MCUXpresso SDK uses the popular west manifest to specify what software is included. This method of delivering software was inspired by [Zephyr](https://github.com/zephyrproject-rtos/zephyr).  

NXP has organized the SDK software into the groups below:
* Device and Board enablement with shared drivers and components
* RTOS software
* Middleware software
* Example projects. Assist in evaluation of the above software

The MCUXpresso SDK west manifest provides the following benefits:
1. Users can modify the included software to align with their application.
2. Software is in smaller repositories to avoid a single large download requirement. The download size is reduced by selecting a custom manifest.

The [Zephyr west tool](https://docs.zephyrproject.org/latest/guides/west/index.html) is used to manage this multi-repo SDK. By providing different manifest files, the user has the flexibility to:
1. Get software for a specific device by listing a product family manifest file (```RT.yml```/```MCX.yml```). The manifest includes fewer folders/files, and provides a faster download experience.  - Will provide in the 25.03.00 release update
2. Retrieve the full MCUXpresso SDK by using the default *west.yml*. This installs all the available MCUXpresso SDK software repositories, and therefore results in the longest download time.
3. Users can create a custom version of the  ```west.yml``` optimized for the needs of their own projects.

# Setup

## Installation/Setup
 - Environment - The easiest way to install all dependencies is using the MCUXpresso Installer. The installer can be installed and launched from the MCUXpresso for VS Code extension. The installer can also be installed from here: https://github.com/nxp-mcuxpresso/vscode-for-mcux/wiki/Dependency-Installation
 - Once the MCUXpresso Installer is launched, install the following:<br>
 **MCUXpresso SDK Developer** <br>
 This automatically installs the following: <br>
     - Homebrew - package manager for macOS
     - CMake - Manages the build process
     - Ninja - Small and fast build system
     - Git - version control system
     - Arm GNU Toolchain - Toolchain for Arm Architecture
     - libncurses5
     - Arm GNU Toolchain add-ons
     - Arm GNU Toolchain Standalone add-ons
     - Python
     - pip - Package installer for Python
     - west - To manage multipe Git repos under a single directory (the west manifest file) <br>
     
    **Debug Probes Software** (Select the ones are needed for your EVK/probe) <br>
     - LinkServer - GDB server and low level debug utilities for NXP probes
     - SEGGER J-Link - Software pack for J-Link debug probes
     - PEmicro - GDB server for debuggin supported NXP ARM Cortex-M targets

 - To install these dependencies manually, follow the steps found on the SDK Documentation https://mcuxpresso.nxp.com/mcuxsdk/latest/html/gsd/installation.html#installation

    
## Cloning the Repo 
- To clone the repo from the CLI run the commands:
    >west init -m https://github.com/nxp-mcuxpresso/mcuxsdk-manifests.git<br>
    west update
    

## Additional steps
- Add ARMGCC_DIR to your environment variables
    - Open your system settings
    - Add ARMGCC_DIR and its path

# Evaluate an example project
Please refer to the guideline [run project](https://mcuxpresso.nxp.com/mcuxsdk/latest/html/gsd/run_project.html).

# Other resources
See our [online documentation](https://mcuxpresso.nxp.com/mcuxsdk/latest/html/index.html)

# Contribution
The contribution is not open now, will open soon.