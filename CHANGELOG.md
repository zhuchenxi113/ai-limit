# Changelog

All notable user-facing changes to AI Limit are documented here.

## 0.3.24 - 2026-08-01

### Added

- First public Windows tray application, built with PySide6.
- Compact bilingual quota panel for Claude Code and Codex usage.
- Configurable automatic refresh interval from 1 to 5 minutes.
- Bilingual Inno Setup installer with Start menu and startup shortcuts.
- Visible in-app update flow with Ed25519 release-signature verification.
- Single-instance protection and separate light/dark tray icons.

### Changed

- Improved Windows panel styling, localization, update dialog, and tray interaction.
- Changing the refresh interval now updates the panel footer immediately.
- Shared usage requests now use more browser-like request metadata and clearer service status handling.

### Security

- Windows updates verify a signed release document, installer hash, filename, PE file format, embedded product version, and Authenticode state before launch.
- The first Windows release is not Authenticode code-signed. Windows may display an “Unknown publisher” or Microsoft Defender SmartScreen warning.

## 0.3.2 - 2026-06-02

### Changed

- Aligned the private development build version with the public v0.3.2 release.

## 0.3.1 - 2026-06-02

### Changed

- Aligned the Chinese and English menu panel widths.
- Updated English reset-time rows to use time-first formatting.

### Fixed

- Mounted installer DMGs at an explicit temporary path to avoid parsing `/Volumes` output.
- Read the app version with `plutil` so DMG packaging works reliably with arbitrary plist paths.

## 0.3.0 - 2026-05-29

### Added

- Added SwiftBar menu bar plugin for Claude Code and CodeX quota monitoring.
- Added menu bar display switching between 5-hour and 7-day quota windows.
- Added Chinese and English menu language switching.
- Added real manual refresh for SwiftBar by bypassing the short local cache.
- Added Claude and CodeX plan display in the SwiftBar detail menu.
- Added relative reset labels such as today, tomorrow, 2 days, and next weekday.

### Changed

- Switched the SwiftBar title to native text for sharper menu bar rendering.
- Improved SwiftBar detail menu alignment and compact quota rows.
- Formatted CodeX plan names as title case, for example Plus instead of PLUS.
- Simplified the menu bar display mode to one global 5-hour or 7-day choice.

### Fixed

- Avoided misleading menu bar values from automatically choosing the weekly quota.
- Reduced menu switching latency with a short SwiftBar usage cache.
- Hid SwiftBar default menu items where supported by SwiftBar metadata.
- Kept SwiftBar quota rows readable without threshold colors by avoiding disabled gray text.
