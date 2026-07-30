# Source

Vendored from [ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd) (MIT).

- Upstream file: `skills/i-have-adhd/SKILL.md`
- Commit: `07684c4ab625dd7d1ea6e99e065f60bc0ac6a1ba`
- Copied: 2026-07-30

`SKILL.md` is unmodified. To update, re-copy from upstream and bump the commit above.

## Always-on hook

`.claude/hooks/always-on.sh` and the `SessionStart` entry in `.claude/settings.json`
are adapted from upstream's `hooks/always-on.sh` + `hooks/hooks.json`. The only
change: the skill path is resolved via `$CLAUDE_PROJECT_DIR` instead of
`$CLAUDE_PLUGIN_ROOT`, since this skill is vendored into the project rather than
installed as a plugin.

Opt in with:

```bash
touch ~/.claude/.i-have-adhd-always
```

Opt out:

```bash
rm ~/.claude/.i-have-adhd-always
```

The hook only fires when that flag file exists, so it changes nothing for
anyone who hasn't created it. `"stop adhd mode"` still turns the ruleset off
for the current session.
