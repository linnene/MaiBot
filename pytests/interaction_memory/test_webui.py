from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services import interaction_memory as module
from src.services.interaction_memory import InteractionMemoryService, InteractionStore
from src.webui.dependencies import require_auth
from src.webui.routers import memory as routes


def client(tmp_path,monkeypatch,authenticated=True):
    service=InteractionMemoryService(InteractionStore(tmp_path/'state.db'))
    monkeypatch.setattr(module,'interaction_memory',service)
    monkeypatch.setattr(routes._chat_manager,'get_existing_session_by_session_id',lambda _:None)
    app=FastAPI();app.include_router(routes.router,prefix='/api/webui')
    if authenticated:app.dependency_overrides[require_auth]=lambda:True
    return TestClient(app),service


def test_panel_and_state_use_existing_auth_and_safe_rendering(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    response=c.get('/api/webui/memory/interaction-panel')
    assert response.status_code==200 and 'textContent' in response.text
    assert 'innerHTML' not in response.text
    state=c.get('/api/webui/memory/interaction')
    assert state.status_code==200 and state.json()['items']==[]


def test_unknown_item_and_invalid_status_are_rejected(tmp_path,monkeypatch):
    c,_=client(tmp_path,monkeypatch)
    assert c.patch('/api/webui/memory/interaction/items/missing',json={'status':'done'}).status_code==404
    assert c.patch('/api/webui/memory/interaction/items/missing',json={'status':'erase_all'}).status_code==422
