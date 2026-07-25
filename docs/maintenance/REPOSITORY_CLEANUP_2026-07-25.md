# Repository Cleanup — 2026-07-25

## Outcome

The original repository was cleaned with Git-aware operations. No trading source,
provider routing, runtime database, secret, analysis history, portfolio state, or
unmerged work was deleted. The cleanup remains isolated on
`chore/repository-cleanup-2026-07-25` and is not merged or pushed.

## 1. Starting points

- Original repository: `D:\EGX_AI_Trader`
- Initial `main` HEAD before preserving the approved PNG work:
  `a7d52101dbe323c176d754e6b0f4c8cd2565d221`
- Approved PNG-only commit:
  `b85056ec2b213832c9496102a0c13cc325e5ef05`
- Clean main HEAD used as the cleanup baseline:
  `b85056ec2b213832c9496102a0c13cc325e5ef05`
- Cleanup branch: `chore/repository-cleanup-2026-07-25`
- Cleanup commit:
  `783bc24a7122510be06f60777b7c7d941a4f4f9e`

## 2. Recovery checkpoint

- Local tag: `pre-cleanup-2026-07-25`
- Tagged commit: `b85056ec2b213832c9496102a0c13cc325e5ef05`
- The tag was not pushed.

## 3. Rubix launcher runtime state

- Original tracked path: `config/rubix_launcher_settings.json`
- External byte-for-byte backup:
  `D:\EGX_AI_Trader_ARCHIVE_2026-07-25\runtime\config\rubix_launcher_settings.json`
- Backup SHA-256:
  `7E6ED8721C81FE3E006E175EEC98180DE6043532767E0A96C6199AE0D8E5F611`
- Preserved stash: `stash@{0}`
- Stash object:
  `8e8f9c4d67fb1e9e99fe846c5310695230af304f`
- Stash message:
  `preserve local Rubix launcher runtime state 2026-07-25`

The earlier setup-report stash is also preserved:

- Current reference: `stash@{1}`
- Immutable stash hash:
  `39a5f0bcfa0fff1ab8ab2da66703c3105c3182ae`
- Message:
  `pre-merge preserve AI_STOCK_ANALYSIS_WORKTREE_SETUP_REPORT.md`
- Preserved untracked file:
  `AI_STOCK_ANALYSIS_WORKTREE_SETUP_REPORT.md`

Neither stash was applied, popped, dropped, cleared, rewritten, or included in
the cleanup commits. No Rubix runtime state was deleted.

## 4. Worktree audit and removals

Removed with `git worktree remove` and without `--force`:

| Worktree | HEAD | Reason safe to remove |
|---|---|---|
| `.claude/worktrees/ai-stock-analysis-core-ade9f1` | `7d52c31a` | Clean, detached, fully contained in main |
| `D:\EGX_AI_Trader_AI_Core` | `2bbea1c2` | Clean, branch fully contained in main |
| `D:\EGX_AI_Trader_AI_Integration_Codex` | `cb8d4e80` | Clean, validated integration fully contained in main |
| `D:\EGX_AI_Trader_AI_UI` | `2bbea1c2` | Commit contained in main; its only untracked artifact was archived first |

`git worktree prune` was run after the removals.

## 5. Worktrees retained

| Worktree | Branch / HEAD | Why retained |
|---|---|---|
| `.claude/worktrees/ai-stock-analysis-verification-bf14a4` | `claude/ai-stock-analysis-verification-bf14a4` / `6fb53cd5` | Clean, but has two commits not contained in main |
| `D:\EGX_AI_Trader_AI_UI_Claude` | `claude/ai-stock-analysis-ui` / `88857a5f` | Clean, but branch ancestry is not fully merged into main |

Detailed pre-merge audit:

1. `D:\EGX_AI_Trader\.claude\worktrees\ai-stock-analysis-verification-bf14a4`
   - branch: `claude/ai-stock-analysis-verification-bf14a4`
   - HEAD: `6fb53cd56c9c514f6b997cd8bf0d79b1b2b50e4b`
   - status: clean; no tracked changes and no untracked files
   - commits unique relative to current main:
     - `d9cdc6b` — deterministic single-symbol AI Stock Analysis core
     - `6fb53cd` — typed-contract, market-phase, provenance, volume-safety and
       evidence-validation corrections
   - retention reason: removing it would discard a branch whose commit ancestry
     is not contained in main, even though validated equivalents were later
     integrated through another branch.
2. `D:\EGX_AI_Trader_AI_UI_Claude`
   - branch: `claude/ai-stock-analysis-ui`
   - HEAD: `88857a5fc6b77a08dd4cdab36e27c5c164a296c6`
   - status: clean; no tracked changes and no untracked files
   - commits unique relative to current main:
     - `d9cdc6b` — deterministic single-symbol backend
     - `6fb53cd` — typed Core contract correction
     - `88857a5` — manual one-symbol UI and Arabic PNG presentation layer
   - retention reason: the branch has a separate, not-fully-merged ancestry and
     must be reviewed or reconciled before its worktree can be removed.

No associated local branch was deleted.

## 6. External archive

Archive root:

`D:\EGX_AI_Trader_ARCHIVE_2026-07-25`

Archived unique/uncertain content:

1. `worktrees\EGX_AI_Trader_AI_UI\$null`
   - zero-byte untracked artifact
   - SHA-256:
     `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`
   - manifest:
     `worktrees\EGX_AI_Trader_AI_UI_MANIFEST.txt`
2. `snapshots\EGX_AI_Trader_RC1`
   - historical release snapshot
   - 152 eligible files preserved
   - 22 files matched the current repository byte-for-byte
   - 6 files were absent from the current repository
   - 124 files differed from the current repository
   - manifest:
     `snapshots\EGX_AI_Trader_RC1_MANIFEST.txt`

The RC1 snapshot was archived rather than deleted because it contains unique
historical release evidence. Its three generated Python cache artifacts were omitted.

## 7. Generated artifacts removed

- 26 `.pytest_cache` / `__pycache__` directories
- 349 generated `.pyc`/`.pyo` files contained by those directories
- `streamlit_runtime.err`
- `streamlit_runtime.out`
- `phase10_streamlit.err`
- `phase10_streamlit.out`

Historical evidence was retained with Git-aware moves:

- `log.txt` → `logs/archive/2026-07-25/log.txt`
- `structure.txt` → `logs/archive/2026-07-25/structure.txt`

## 8. Documents and audit evidence organized

- 73 tracked root Markdown documents were classified by their headings and content.
- Root project reports now live under:
  - `docs/audits/architecture`
  - `docs/audits/providers`
  - `docs/audits/research`
  - `docs/audits/strategies`
  - `docs/audits/ai-stock-analysis`
  - `docs/audits/regression`
  - `docs/guides/operations`
  - `docs/guides/launcher`
  - `docs/guides/authentication`
  - `docs/guides/backup`
  - `docs/archive/historical/phases`
- Internal Markdown links and application guide paths were updated.
- `rr_audit.csv` → `reports/audits/rr_audit.csv`
- `provider_selection_report.csv` →
  `reports/audits/provider_selection_report.csv`
- `analyze_losses.py` and `analyze_timeout.py` moved from the root to `scripts/`.

Only `README.md` remains as a Markdown entry document at repository root.

## 9. Frozen-engine validation portability

The full test run exposed two pre-existing Windows portability assumptions:

1. byte hashes changed when Git checked text out as CRLF;
2. one test depended on the ignored duplicate `EGX_AI_Trader_RC1` directory.

No frozen strategy file was modified. The RC1 and current frozen engine files were
confirmed textually identical after newline normalization. The validation manifest and
tests now hash normalized text and no longer depend on a duplicate source snapshot.

## 10. Deliberately retained

All protected source and runtime areas were retained, including:

- `core`, `dashboard`, `services`, `providers`, `config`, `tests`
- all strategy, indicator, backtesting, portfolio, decision-support and optimization code
- `data`, `reports`, `logs`, `backups`, `outputs`, and `venv`
- `.env` (present and ignored)
- append-only `data/ai_analysis/history.jsonl`
- Rubix, EODHD, forward-testing, paper-portfolio, scalping and research databases
- launcher/startup files, requirements files, `app.py`, `main.py`, and `backtest.py`

## 11. `.gitignore`

No change was required. Existing rules already cover:

- `.env` and secret formats
- Python/pytest caches
- runtime `.err`/`.out` files
- databases and SQLite sidecars
- generated reports, logs, backups and outputs
- the former in-repository RC1 snapshot path

## 12. Validation

| Validation | Result |
|---|---|
| AI Stock Analysis suite | 153 passed |
| PNG card tests | 41 passed |
| Unified/Rubix launcher suite | 78 passed |
| Full repository suite | 769 passed |
| Streamlit smoke | `STREAMLIT_SMOKE_OK` |
| Temporary PNG generation | 1080×1350, successful |
| Markdown relative-link audit | 0 broken links |
| `git diff --check` | passed |

The AI Stock Analysis integration tests reconfirm one-symbol-only analysis, EODHD
current research, Rubix live/auction separation, honest deterministic narrative
labelling, Production Disabled, and no operational Yahoo call/comparison. No provider
routing or trading behavior changed during cleanup.

## 13. Final root listing

Files:

`.env`, `.gitignore`, `app.py`, `backtest.py`, `main.py`, `pytest.ini`, `README.md`,
`requirements-dev.txt`, `requirements-lock.txt`, `requirements.txt`

Principal directories:

`ai`, `backtesting`, `backups`, `config`, `core`, `dashboard`, `data`,
`decision_support`, `docs`, `forward_testing`, `indicators`, `logs`, `optimization`,
`outputs`, `portfolio`, `providers`, `reports`, `scalping`,
`scalping_expected_range`, `scripts`, `services`, `strategy`, `strategy_breakout`,
`strategy_selector`, `tests`, `venv`

Repository/tool metadata directories remain intentionally present.

## 14. Final registered worktrees

1. `D:\EGX_AI_Trader` — `chore/repository-cleanup-2026-07-25`
2. `D:\EGX_AI_Trader\.claude\worktrees\ai-stock-analysis-verification-bf14a4`
3. `D:\EGX_AI_Trader_AI_UI_Claude`

## 15. Safety confirmations

- No source or trading logic was deleted or changed.
- No runtime/user data was lost.
- No `.env` value was printed or moved.
- No database was deleted, rewritten, or copied into Git.
- No dirty or unmerged worktree was removed.
- No branch or stash was deleted.
- The cleanup branch was not merged and nothing was pushed.

## 16. Final pre-merge audit

### Commit chain

- Current branch: `chore/repository-cleanup-2026-07-25`
- Main HEAD: `b85056ec2b213832c9496102a0c13cc325e5ef05`
- Merge-base with main:
  `b85056ec2b213832c9496102a0c13cc325e5ef05`
- Approved PNG commit:
  `b85056ec2b213832c9496102a0c13cc325e5ef05`
- Repository cleanup commit:
  `783bc24a7122510be06f60777b7c7d941a4f4f9e`
- Corrective settings-restoration commit:
  `7ac0ad76f654683e5516ae9b55af558bb9b615df`

The commits remain separate and ordered. No commit was squashed, amended, reset,
or otherwise rewritten.

### `config/settings_manager.py` audit

The cleanup commit changed one comment:

```diff
-    # See TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md for the compliance basis.
+    # See docs/audits/providers/TRADINGVIEW_ACCESS_CAPABILITY_REPORT.md.
```

This was a documentation-path update inside Python source. It was not a
line-ending-only change and it did not alter settings defaults, provider
selection, runtime configuration, or any executable behavior. Because tests and
runtime do not depend on this comment, retaining it was not strictly required.
The corrective commit restored the file exactly to its pre-cleanup/main state,
eliminating unnecessary source churn.

### Document-move validation

- Git classified all reorganized reports as renames, preserving file history.
- No tracked document has a standalone deletion status.
- No useful report was deleted; uncertain historical content was archived.
- Internal references and script-held paths required by moved files were updated.
- The Markdown relative-link audit found `0` broken links.
- README and launcher-referenced guides resolve to existing files.
- Only `README.md` remains as a root-level Markdown entry point.

### Final revalidation after restoration

| Validation | Result |
|---|---|
| PNG card tests | 41 passed |
| AI Stock Analysis tests | 153 passed |
| Unified/Rubix launcher tests | 78 passed |
| Full repository suite | 769 passed |
| Streamlit smoke | `STREAMLIT_SMOKE_OK` |
| Temporary PNG generation | 1080×1350, 145,991 bytes, removed after verification |
| Markdown relative-link audit | 0 broken links |
| `git diff --check` | passed |

The feature tests reconfirm EODHD current research, Rubix live data with closing
auction kept separate, one-requested-symbol analysis, no operational Yahoo call
or comparison, honest `Deterministic Fallback` labelling, and
`Production Disabled`. No trading, strategy, AI, ranking, provider, portfolio,
risk, indicator, entry, exit, backtest, replay, or forward-testing behavior was
changed.

After committing this audit update, the cleanup worktree is clean. Both stashes,
the external Rubix backup, runtime databases, `.env`, analysis history and all
user/runtime data remain intact. The branch remains unmerged and unpushed.
