import re


def win_to_wsl(path: str) -> str:
    """Convert Windows path to WSL2 path.
    C:\\Users\\Qasim\\Desktop → /mnt/c/Users/Qasim/Desktop
    Also handles already-correct /mnt/ and /home/ paths.
    """
    if not path:
        return path
    if path.startswith('/'):
        return path
    path = path.replace('\\', '/')
    match = re.match(r'^([a-zA-Z]):/(.*)', path)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        return f"/mnt/{drive}/{rest}"
    return path


def wsl_to_win(path: str) -> str:
    """Convert WSL2 path to Windows path for PowerShell commands.
    /mnt/c/Users/Qasim → C:\\Users\\Qasim
    """
    if not path:
        return path
    match = re.match(r'^/mnt/([a-z])/(.*)', path)
    if match:
        drive = match.group(1).upper()
        rest = match.group(2).replace('/', '\\')
        return f"{drive}:\\{rest}"
    return path


def safe_path(path: str) -> str:
    """Always returns a safe WSL2 path regardless of input format."""
    return win_to_wsl(path)
