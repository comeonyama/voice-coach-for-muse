"""Single hook invocation; inherits fresh platform credentials, no daemon."""
import argparse
import json
import pathlib
import subprocess
import sys
from runtime import file_lock, read_state, valid_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    cfg = json.loads(pathlib.Path(args.config).read_text())
    root = pathlib.Path(__file__).resolve().parent
    state = pathlib.Path(cfg['state_dir'])
    try:
        with file_lock(state / '.hook.lock', blocking=False):
            watcher = [sys.executable, str(root / 'session_watcher.py'), '--once', '--config', args.config]
            subprocess.run(watcher, check=True)
            session = read_state(state / 'session.json', {}, valid=valid_session)
            if session.get('state') == 'live':
                subprocess.run([sys.executable, str(root / 'coach_monitor.py'), '--once',
                                '--config', args.config, '--session', str(state / 'session.json')], check=False)
    except BlockingIOError:
        return


if __name__ == '__main__':
    main()
