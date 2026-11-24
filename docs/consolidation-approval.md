# Proposed approval operations

Main is occupied by the original worktree. It remains at 5be4131; do not
update that reference behind its worktree. The integration result is ready for
a reviewed fast-forward in that worktree once owner approval covers updating
its checked-out main. Recheck clean status and remote ancestry first.

```bash
git -C /home/erikmischi/Desktop/Projects/M.I.R.A merge --ff-only integration/repository-consolidation
git push origin main:main
git branch -d ftr/llm_async_integration refactor/embodiment-frame refactor/embodiment-vocabulary
git push origin --delete feat/device-info-providers feat/embodiment-micro-motions feat/nonverbal_voice feat/notch-embodiment-ui feat/notch-notification-priority feat/notch-timer feat/perception-camera-foundation feat/perception-human-presence feat/usb-device-detection fix/notch-integration-hardening fix/notch-notice-floor integration/notch-unified refactor/embodiment-frame
```

No other refs will be deleted. Archive and experiment aliases remain local. No worktree removal is proposed. If remote main has advanced, stop publication and reconcile it first.
