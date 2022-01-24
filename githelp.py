import pathlib
import subprocess


class GitRepo:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path

    def checkout_branch(self, branch: str):
        subprocess.run(
            ["git", "checkout", branch],
            check=True,
            cwd=self.path,
        )

    def commit(self, relpath: str, msg: str):
        subprocess.run(
            ["git", "commit", "-F", "-", relpath],
            check=True,
            cwd=self.path,
            input=msg,
            text=True,
        )

    def get_current_branch(self) -> str:
        branch = subprocess.check_output(
            ["git", "symbolic-ref", "HEAD", "--short"], cwd=self.path
        ).decode("utf-8")
        return branch.strip()

    def get_last_tag(self) -> str:
        output = subprocess.check_output(
            ["git", "describe", "--abbrev=0", "--tags"], cwd=self.path
        ).decode("utf-8")
        return output.strip()

    def is_file_dirty(self, relpath: str) -> bool:
        output = subprocess.check_output(
            ["git", "status", "--porcelain", relpath], cwd=self.path
        ).decode("utf-8")
        return output != ""

    def make_tag(self, tag: str):
        subprocess.run(["git", "tag", tag], check=True, cwd=self.path)

    def pull(self):
        subprocess.run(["git", "pull"], check=True, cwd=self.path)

    def push(self):
        subprocess.run(["git", "push"], check=True, cwd=self.path)

    def push_tag(self, tag: str):
        subprocess.run(["git", "push", "origin", tag], check=True, cwd=self.path)

    def reset_origin(self, branch: str):
        subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=self.path)
