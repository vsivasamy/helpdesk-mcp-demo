"""Run a command with variables from dotenv file(s), parsed safely (no shell evaluation, so URLs
containing '&' work). Later files override earlier ones; KEY=value maps can rename on the fly.

  python scripts/with_env.py .neon.env -- python db/seed.py
  python scripts/with_env.py .neon.env DATABASE_URL=@NEON_RO_URL -- python -m app.server
(`NAME=@OTHER` copies the value of OTHER; never prints values.)
"""
import os
import sys


def parse(path: str) -> dict[str, str]:
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip().removeprefix("export ").strip()] = v
    return out


def main() -> None:
    args = sys.argv[1:]
    if "--" not in args:
        sys.exit(__doc__)
    i = args.index("--")
    env = dict(os.environ)
    for a in args[:i]:
        if "=" in a:
            k, v = a.split("=", 1)
            if v.startswith("@"):
                v = env[v[1:]]
            if v == "":
                env.pop(k, None)
            else:
                env[k] = v
        else:
            env.update(parse(a))
    os.execvpe(args[i + 1], args[i + 1:], env)


if __name__ == "__main__":
    main()
