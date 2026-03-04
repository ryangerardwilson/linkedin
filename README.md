# linkedin

Minimal CLI to post to LinkedIn from the command line.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set LinkedIn OAuth app credentials:

```bash
export LINKEDIN_CLIENT_ID="..."
export LINKEDIN_PRIMARY_CLIENT_SECRET="..."
export LINKEDIN_SECONDARY_CLIENT_SECRET="..."
```

`LINKEDIN_CLIENT_SECRET` is also supported and takes priority if set.

LinkedIn Developer Console callback URL for this flow:

```text
https://callback-omega-one.vercel.app/callback/linkedin
```

Set that same value in runtime if needed (optional, already default):

```bash
export LINKEDIN_OAUTH2_REDIRECT_URI="https://callback-omega-one.vercel.app/callback/linkedin"
```

Generate a user access token manually:

```bash
python oauth2_login.py --client-id "$LINKEDIN_CLIENT_ID"
```

By default token JSON is stored at `~/.linkedin/oauth2_token.json`, which `main.py` reads automatically.
If no valid token is found when posting, `main.py` auto-starts this login flow.

Token env var overrides also supported:

```bash
export LINKEDIN_USER_ACCESS_TOKEN="..."
```

Also accepted: `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_BEARER_TOKEN`.

## Usage

Post directly:

```bash
python main.py "hello, world"
```

Post with image media:

```bash
python main.py "hello, world" /path/to/image.jpg
```

or:

```bash
python main.py "hello, world" --media /path/to/image.jpg
```

Compose in Vim:

```bash
python main.py -e
```

## Media Notes

- This CLI currently supports image attachment uploads for LinkedIn posts.
- Videos/GIFs are not implemented yet in this project.

## CLI Flags

- `-e`, `--edit`: Open Vim to compose a post.
- `-m`, `--media`: Attach an image from a local file path.
- `-v`, `--version`: Print version and exit.
- `-u`, `--upgrade`: Upgrade via the installer script.
- `-h`, `--help`: Show help.

## Install (binary release)

```bash
curl -fsSL https://raw.githubusercontent.com/ryangerardwilson/linkedin/main/install.sh | bash
```

## Release workflow

Tags like `v0.1.0` trigger GitHub Actions to build `linkedin-linux-x64.tar.gz` and publish a release.
