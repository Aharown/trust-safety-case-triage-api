import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import Base, get_db, engine, SessionLocal, DATABASE_URL

TEST_DATABASE_URL = "postgresql://localhost/case_triage_test"

engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

if "localhost" not in DATABASE_URL:
    raise RuntimeError("Refusing to run tests against a non-local database")


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def override_get_db():
    connection = engine.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


app.dependency_overrides[get_db] = override_get_db


def override_get_db_committing():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
