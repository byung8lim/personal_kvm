"""Host-free verification of installer dependency selection branches.

Mocked rpm/repoquery commands; never invokes system package installation.
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / 'scripts/install.sh').read_text(encoding='utf-8')
FUNCTIONS = SRC[SRC.index('repo_has_package() {'):SRC.index('# 설치가 실패해도 임의')]


class CompatibilityPackageSelectionTests(unittest.TestCase):
    def evaluate(self, installed, available, candidates):
        def quote(s):
            return "'" + s.replace("'", "'\\''") + "'"
        script = """rpm() { [[ "$1" == '-q' && ":$MOCK_INSTALLED:" == *":$2:"* ]]; }
# mock repo_has_package; no external dnf invocation
""" + FUNCTIONS + "\nrepo_has_package() { [[ \":$MOCK_AVAILABLE:\" == *\":$1:\"* ]]; }\n" + \
            'choose_package ' + ' '.join(quote(x) for x in candidates)
        result = subprocess.run(['bash', '-c', script], env={
            'PATH': '/usr/bin:/bin',
            'MOCK_INSTALLED': ':'.join(installed),
            'MOCK_AVAILABLE': ':'.join(available),
        }, capture_output=True, text=True, check=False)
        return result.returncode, result.stdout.strip()

    def test_rocky9_prefers_available_mysql_server(self):
        self.assertEqual(self.evaluate([], ['mysql-server','mariadb-server'],
                                       ['mysql-server','mariadb-server']), (0, 'mysql-server'))

    def test_rocky9_falls_back_to_mariadb(self):
        self.assertEqual(self.evaluate([], ['mariadb-server'],
                                       ['mysql-server','mariadb-server']), (0, 'mariadb-server'))

    def test_rocky10_package_mapping_is_available(self):
        self.assertEqual(self.evaluate([], ['guestfs-tools'],
                                       ['guestfs-tools','libguestfs-tools']), (0, 'guestfs-tools'))

    def test_unknown_package_fails_closed(self):
        self.assertEqual(self.evaluate([], [], ['not-in-repo'])[0], 1)

    def test_installed_package_is_accepted_without_repo(self):
        self.assertEqual(self.evaluate(['libguestfs-tools'], [],
                                       ['guestfs-tools','libguestfs-tools']), (0,'libguestfs-tools'))

if __name__ == '__main__':
    unittest.main()
