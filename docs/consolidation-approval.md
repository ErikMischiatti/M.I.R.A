# Approved operations — executed

The owner approved these operations. Main was fast-forwarded safely in its
clean worktree and published. Python 3.12.16 CI passed before cleanup. The
commands below are the historical approval plan, not commands to rerun.
See [final execution results](consolidation.md#approved-publication-and-cleanup-results).

```bash
git -C /home/erikmischi/Desktop/Projects/M.I.R.A merge --ff-only integration/repository-consolidation
git push origin main:main
git branch -d ftr/llm_async_integration refactor/embodiment-frame refactor/embodiment-vocabulary
git push origin --delete feat/device-info-providers feat/embodiment-micro-motions feat/nonverbal_voice feat/notch-embodiment-ui feat/notch-notification-priority feat/notch-timer feat/perception-camera-foundation feat/perception-human-presence feat/usb-device-detection fix/notch-integration-hardening fix/notch-notice-floor integration/notch-unified refactor/embodiment-frame
```

No other refs will be deleted. Archive and experiment aliases remain local. No worktree removal is proposed. If remote main has advanced, stop publication and reconcile it first.

Execution exception: `refactor/embodiment-frame` was already absent remotely;
its stale local tracking ref was removed instead. All other approved deletions completed.
