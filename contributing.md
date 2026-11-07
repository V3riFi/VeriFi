# Contributing to VeriFi

We have 6 people and one weekend. These rules exist so nobody overwrites anybody's work.

## The golden rules

1. **Pull before you start, pull before you push.**
2. **Never force push** (`git push -f`). It can wipe other people's commits. On `main` it's blocked anyway: a ruleset stops force pushes to `main` and deleting it. Normal pushes and PR merges to `main` still work.
3. **Work on a branch, not on `main`.**
4. **Only touch your own folder** unless you've told the owner.
5. **Don't push something that breaks the main run.** If it's half-done, keep it on your branch.

## Repo location

The repo lives at **https://github.com/V3riFi/VeriFi** (it moved from Cohen's personal account). GitHub redirects the old URL, but point your clone at the new one anyway:

```
git remote set-url origin https://github.com/V3riFi/VeriFi.git
git remote -v
```

Both lines from `git remote -v` should show `V3riFi/VeriFi`. If you haven't cloned yet, just clone the new URL (see README).

## Who owns what

| Folder | Owner(s) |
|---|---|
| `firmware/` | Hardware team |
| `vision/` | Aagrim, Cohen |
| `backend/` | Matthew |
| `frontend/` | Aaryan, Matthew |
| `README.md`, `requirements.txt` | Anyone, but say so in the group chat |

## Git workflow

Start of every work session:

```
git switch main
git pull
git switch -c yourname/what-youre-doing
```

Example branch names: `aagrim/blink-rate`, `matthew/websocket`, `aaryan/live-charts`.

Save your work often:

```
git add <files you changed>
git commit -m "Add blink rate from eye aspect ratio"
git push -u origin yourname/what-youre-doing
```

(After the first push, plain `git push` works.)

When it works, merge it into main:

1. Open a Pull Request on GitHub from your branch into `main`.
2. No review required. If GitHub says there are no conflicts, merge it.
3. If there **are** conflicts, grab the person who owns the other file and fix them together.
4. Tell the group chat you merged.

Already on your branch and want the latest main?

```
git switch main
git pull
git switch yourname/what-youre-doing
git merge main
```

## Python environment

Always work inside the venv (see README). If you need a new package:

1. `pip install <package>`
2. Add **one line** with its name to `requirements.txt` by hand.
3. Commit it and tell the group chat so everyone runs `pip install -r requirements.txt`.

Never run `pip freeze > requirements.txt`. It pins every sub-dependency for your OS, which can break installs on the other OS (we have both Mac and Windows) and causes merge conflicts.

## Frontend packages

Install with `npm install <package>` inside `frontend/`. Commit `package.json` **and** `package-lock.json`, never `node_modules/`. After pulling, run `npm install` again if either file changed.

## Webcam and serial port

Only the backend opens the webcam and the Arduino's serial port. Don't call `getUserMedia` in the frontend or run a separate script that opens the camera or port while the backend is running. The second program will fail or block the first. Close the Arduino Serial Monitor/Plotter before starting the backend.

## Do not commit

- `.venv/`
- `data/` (recorded sessions contain people's physiological data and webcam info, keep them local)
- `face_landmarker.task` and other large model files
- API keys or `.env` files
- `node_modules/`

Put this in `.gitignore` at the repo root:

```
.venv/
__pycache__/
*.pyc
data/
*.task
.env
node_modules/
.DS_Store
```

## Commit messages

Short, present tense, says what changed: `Add GSR baseline calculation`, `Fix serial port crash on Windows`. Not `stuff`, `fix`, or `asdf`.

## Interfaces between parts

Don't change these without telling everyone, since other people's code depends on them:

- **Serial protocol** (firmware <-> backend): see README.
- **WebSocket messages** (backend <-> frontend): `ws://localhost:8000/ws`, JSON, one object per update. Add new fields freely; don't rename or remove existing ones without asking.
- **Vision output** (vision <-> backend): a dict of features per frame window, e.g. `{"blink_rate": 18.0, "hand_on_face": false}`.

## Testing with real people

- Ask before putting sensors or a camera on anyone, and tell them what's recorded.
- Save sessions under `data/` with no names in the filename (use `subject01`, `subject02`, ...).
- Delete recordings after the hackathon unless the person agreed to keep them.

## Stuck?

If you've been stuck for 20+ minutes, say so in the group chat. That's normal, and it saves the team time.