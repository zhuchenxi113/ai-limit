"""Run the Windows updater's local download and verification gate.

This helper intentionally stops before ``trigger_interactive_install``.  It is
used to prove which local feed cases are allowed to reach the launch boundary;
the visible installer launch remains a separate, explicitly confirmed step.
"""

import argparse
import os
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--expect", choices=("allow", "reject"), required=True)
    parser.add_argument("--reason")
    args = parser.parse_args()

    os.environ["AI_LIMIT_RELEASE_FEED_OVERRIDE"] = args.manifest.resolve().as_uri()
    import updater_win

    info = updater_win.fetch_latest_release_info()
    latest = info.get("latest", "")
    newer = (updater_win._version_tuple(latest) >
             updater_win._version_tuple(args.current_version))
    launch_gate = False
    trust = ""
    reason = ""
    downloaded_setup = False
    downloaded_signature = False

    if not newer:
        reason = "not_newer"
    elif not info.get("asset_url"):
        reason = "missing_installer_asset"
    elif not info.get("signature_url"):
        reason = "missing_signature_asset"
    else:
        dest = pathlib.Path(tempfile.mkdtemp(prefix="ai-limit-update-acceptance-"))
        try:
            setup = updater_win.download_release_setup(info["asset_url"], dest)
            downloaded_setup = True
            signature = updater_win.download_release_signature(
                info["signature_url"], dest
            )
            downloaded_signature = True
            trust = updater_win.verify_installer(
                setup, signature, latest, info["asset_name"]
            )
            launch_gate = True
        except updater_win.UpdateFailed as error:
            reason = error.reason

    print(
        f"current={args.current_version} latest={latest} newer={newer} "
        f"asset={info.get('asset_name')} signature={info.get('signature_name')}"
    )
    print(
        f"downloaded_setup={downloaded_setup} "
        f"downloaded_signature={downloaded_signature} trust={trust or '-'} "
        f"launch_gate={launch_gate} reject_reason={reason or '-'}"
    )

    expected_allow = args.expect == "allow"
    if launch_gate != expected_allow:
        return 1
    if args.reason and reason != args.reason:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
