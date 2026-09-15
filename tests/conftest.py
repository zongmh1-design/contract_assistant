from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.integrations.approval import MockApprovalGateway
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database_path = (tmp_path / "test.db").as_posix()
    app = create_app(
        database_url=f"sqlite:///{database_path}",
        approval_gateway=MockApprovalGateway(),
        contract_storage_root=tmp_path / "storage" / "contracts",
    )
    with TestClient(app) as test_client:
        yield test_client


def sync_all(client: TestClient) -> list[dict]:
    response = client.post("/api/tasks/sync", params={"limit": 4})
    assert response.status_code == 200
    return response.json()["tasks"]
