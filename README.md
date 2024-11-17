# MCUXpresso SDK: mcuxsdk

Welcome to our new Github project MCUXpresso SDK: <u>mcuxsdk</u>!

Similar as our previous Github SDK project [mcux-sdk](https://github.com/nxp-mcuxpresso/mcux-sdk), our new SDK is also composed of separate project(multiple github repos) deliveries. This new SDK improves a lot from the previous [mcux-sdk](https://github.com/nxp-mcuxpresso/mcux-sdk) project especially on the build system, repo division, thus we use this new manifest repo as a new starting point. The improvements are listed as below:

- Explosive improvement in build system
  - Improves software integration and portability based on "component" concept.
  - Provides comprehensive dependency resolve mechanism for "component" with cmake and Kconfig.
  - Fully support all mainstream embedded toolchains: iar, mdk, armgcc, codewarrior, xtensa and riscvllvm.
  - Support IDE project generation for iar, mdk, codewarrior and xtensa to provide OOBE from build to debug.
  - Support standalone project generation to export designated projects to zip and share.
- Individual device repos by series to avoid huge size of a single device repo
- Increased integration of middleware/RTOS projects
  - Almost all the middleware/RTOS you see in our SDK package are now available in this new project.

## Resources
See online documentation at [MCUXpresso SDK](https://mcuxpresso.nxp.com/mcuxsdk/latest/).

## Contribution
The contribution is not open now, will open soon.