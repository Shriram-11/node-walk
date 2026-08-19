"""Simple flat Python project for testing the analyzer."""


MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30.0


class UserService:
    """Manages user operations."""

    def __init__(self, repo):
        self.repo = repo
        self._cache = {}

    def create_user(self, name: str, email: str):
        """Create a new user and save to repository."""
        user = {"name": name, "email": email}
        result = self.repo.save(user)
        self._notify(name)
        return result

    def get_user(self, user_id: int):
        if user_id in self._cache:
            return self._cache[user_id]
        return self.repo.find(user_id)

    def _notify(self, name: str):
        send_email(name)


class UserRepository:
    """Database access for users."""

    def save(self, user: dict):
        return user

    def find(self, user_id: int):
        return None


def send_email(recipient: str) -> bool:
    """Send a notification email."""
    return True


def bootstrap():
    repo = UserRepository()
    svc = UserService(repo)
    return svc
