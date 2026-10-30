"""Udgiv den byggede side til en gren i et git-repository (GitHub, GitLab, Codeberg, Gitea ...).

Grenen overskrives med ét enkelt commit hver gang, så repo'et ikke vokser med daglige datafiler.
Kildekoden ligger på 'main' og opdateres som normalt med git push.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime

log = logging.getLogger(__name__)

GITLAB_CI = """# Bruges kun af GitLab Pages – ignoreres af GitHub/Codeberg
pages:
  stage: deploy
  script:
    - mkdir -p public && find . -maxdepth 1 ! -name . ! -name public ! -name .git -exec cp -r {} public/ \\;
  artifacts:
    paths: [public]
  rules:
    - if: $CI_COMMIT_BRANCH == "pages"
"""


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def publish(cfg: dict) -> bool:
    p = cfg["publish"]
    if not p.get("enabled") or not p.get("remote"):
        log.info("Udgivelse er slået fra (publish.enabled / publish.remote i config.yaml)")
        return False
    work = os.path.join(cfg["data_dir"], "publish")
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(cfg["site_dir"], work)
    open(os.path.join(work, ".nojekyll"), "w").close()
    with open(os.path.join(work, ".gitlab-ci.yml"), "w") as f:
        f.write(GITLAB_CI)
    if p.get("cname"):
        with open(os.path.join(work, "CNAME"), "w") as f:
            f.write(p["cname"].strip() + "\n")
    branch = p.get("branch") or "pages"
    _git(work, "init", "-q", "-b", branch)
    _git(work, "config", "user.name", p.get("author_name") or "Vinjagt")
    _git(work, "config", "user.email", p.get("author_email") or "vinjagt@localhost")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"Opdatering {datetime.now():%Y-%m-%d %H:%M}")
    _git(work, "push", "-q", "--force", p["remote"], f"HEAD:{branch}")
    log.info("Udgivet til %s (gren %s)", p["remote"], branch)
    return True
