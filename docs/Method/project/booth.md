# Photobooth Booth — Project bindings

## Repositories

| Repository | Role |
|---|---|
| `starkerflamingo32/photobooth` (public), branch `booth` | This fork: booth code, the three documentation planes, all tests and test workflows ([test infrastructure](test-infrastructure.md)). |
| upstream `PhotoboothProject/photobooth`, branch `dev` | Merged into `booth`; the booth builds on it and does not diverge from it outside the booth components. |
| `photobooth-setup` (private) | Deployment: Docker test stack (`compose.yaml`, `cups/`, `dummy-dslr/`, `web-ftp/`) and native install on the event laptop (`native/`). No application source. |

## Upstream code

- Upstream's contribution rules apply to code in this fork: [`../../contributing.md`](../../contributing.md).
- `[user]` Booth components go in their own files. Where upstream code must change (e.g. the "Drucker" button in
  `login/menu.php`, the print entry points behind the Busy Gate), the change is the smallest hook that calls into
  booth code, so upstream merges stay small. A hook is as small as the change allows; replacing a whole upstream flow
  (e.g. the guest print screen) takes a larger one.
- Upstream workflows the fork does not need are disabled, not deleted
  ([test infrastructure → GitHub Actions plan](test-infrastructure.md#github-actions-plan)).

## Layers

The component layers are defined in the FSD ([§2.4](../../Functionality/fsd.md#24-component-layering)): L2
application logic → L1 interfaces → L0 foundation, dependencies pointing downward only.

**Source layout: not decided yet.** Which process reads CUPS and queries the printer, where the tracker runs and how
the UI gets updates are open (FSD §2.3). This section names the directory per component once that is decided.

## Commands

Run before every commit (as upstream's `build.yml` does):

```sh
npm install && npm run build      # also formats JS/SCSS and the language file
composer cgl:ci                   # PHP code style
composer lint                     # PHP syntax
composer phpstan                  # static analysis
composer phpunit                  # PHP unit tests
npm run eslint                    # JS lint
```

The integration and e2e suites run against the virtual test setup
([test infrastructure](test-infrastructure.md#virtual-test-setup)).

## Conventions

- **Commit messages**: imperative summary line; FSD changes start with `FSD v<version>: <what changed>` and bump
  `fsd_version` in Appendix D with one `change_history` line.
- **Language** `[user]`: code, comments, commits and the three planes in English; guest and operator texts in German, worded
  exactly as in FSD Appendices B and C.

## Credentials

- The fork's CI reads `photobooth-setup` with a fine-grained, read-only token on that one repository, stored as a
  GitHub Actions secret of the fork.
- The event laptop is reached over SSH with a key from the development VM; `sudo` there needs a password, so
  root steps are run by the booth owner on the laptop.
- No credential, Wi-Fi password or event photo is ever committed or uploaded as a test artifact.
