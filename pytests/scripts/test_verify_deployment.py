from pathlib import Path
import subprocess
import pytest
from scripts.verify_deployment import verify_checkout, resolve_plugin_path

@pytest.fixture
def checkout(tmp_path: Path):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    git('init','-b','dev');git('config','user.name','Synthetic tester');git('config','user.email','test@example.invalid')
    (root/'main.py').write_text('print("synthetic")\n');(root/'.gitignore').write_text('config.toml\ndata/\n')
    git('add','main.py','.gitignore');git('commit','-m','synthetic fixture')
    return root,git('rev-parse','HEAD')

def test_matches_exact_revision(checkout):
    root,sha=checkout
    assert verify_checkout(root,sha)==sha

def test_changed_source_is_rejected(checkout):
    root,sha=checkout;(root/'main.py').write_text('print("changed")\n')
    with pytest.raises(ValueError,match='uncommitted'):verify_checkout(root,sha)

def test_untracked_source_is_rejected(checkout):
    root,sha=checkout;(root/'extra.py').write_text('unexpected = True\n')
    with pytest.raises(ValueError,match='uncommitted'):verify_checkout(root,sha)

def test_private_ignored_configuration_is_not_rejected(checkout):
    root,sha=checkout;(root/'config.toml').write_text('private = "synthetic-value"\n')
    assert verify_checkout(root,sha)==sha

def test_wrong_commit_is_rejected(checkout):
    root,sha=checkout
    with pytest.raises(ValueError,match='revision'):verify_checkout(root,'0'*40)

def test_plugin_path_cannot_escape_instance(tmp_path):
    with pytest.raises(ValueError,match='folder'):resolve_plugin_path(tmp_path,'../other')
