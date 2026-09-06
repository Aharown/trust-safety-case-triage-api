from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_cases_without_role_header_fails():
    response = client.get("/cases")
    assert response.status_code == 422


def test_get_cases_as_reporter_forbidden():
    response = client.get("/cases", headers={"X-Role": "reporter"})
    assert response.status_code == 403


def test_get_cases_as_agent_allowed():
    response = client.get("/cases", headers={"X-Role": "agent"})
    assert response.status_code == 200
