# Contributing to AI Limit

Thanks for helping improve AI Limit. Please keep changes focused and avoid including browser profiles, cookies, build outputs, signing keys, or other machine-local data.

## Run the tests

Install the project dependencies and pytest in a virtual environment, then run both supported test entry points:

```powershell
python -m pip install -r requirements.txt pytest
python -m pytest -q
python -m unittest discover -s tests
```

The two commands intentionally exercise both pytest-based and standard-library unittest discovery.

## Build the Windows application

Use native Windows PowerShell with Python 3.13, PyInstaller, and Inno Setup 6 installed:

```powershell
python -m pip install -r requirements.txt pyinstaller
Set-Location menubar\windows
pyinstaller pyinstaller.spec --noconfirm --clean
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

The build uses the tracked `menubar/windows/icon/ai-limit.ico`. The maintainer-only icon source generator is not part of the public repository and is not needed for normal builds.

Contributor builds do not require the private Windows update-signing key. The signing script and public verification key are public by design; the private key used for official release signatures is not.

Do not commit `build/`, `dist/`, `dist_installer/`, private keys, signature working files, or locally generated installer artifacts.

## Run the macOS application

The macOS menu bar application remains under `menubar/`. Install Python and the dependencies from `requirements.txt`, then follow the macOS instructions in the README.

## Pull requests

- Explain the user-visible behavior being changed.
- Add or update tests for logic changes.
- Confirm the relevant test commands pass on the platform you changed.
- Keep platform-specific code in its existing platform directory unless a cross-platform refactor is the purpose of the change.
