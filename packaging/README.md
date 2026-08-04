# Windows release build

Run from PowerShell:

    powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1

The script creates:

- dist\TSDP\TSDP.exe and its private runtime
- release\TSDP-3.2.0-portable.zip
- release\TSDP-Setup-3.2.0.exe
- release\SHA256SUMS.txt

The installer is per-user by default, supports upgrades over the existing version,
and does not remove settings, logs, or processing jobs during uninstall. Inno
Setup 6 is required only to compile the final setup executable.