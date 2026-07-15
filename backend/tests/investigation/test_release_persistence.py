from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from app.auth import seed_auth_and_release, settings
from app.db import SessionLocal, engine
from app.models import Base, ReleaseNote


@unittest.skipUnless(os.getenv("AIOPS_TEST_DATABASE_URL"), "requires isolated PostgreSQL")
class ReleaseSeedPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def test_existing_version_tracks_latest_deployed_commit(self) -> None:
        first = {
            "app_version": "9.9.9-dev",
            "app_release_title": "First title",
            "app_release_summary": "First summary",
            "app_release_changes": '["first"]',
            "git_commit": "old-commit",
            "bootstrap_admin_password": "temporary-bootstrap-password",
        }
        second = {
            **first,
            "app_release_title": "Updated title",
            "app_release_summary": "Updated summary",
            "app_release_changes": '["updated", "verified"]',
            "git_commit": "new-commit",
        }
        with patch.multiple(settings, **first):
            async with SessionLocal() as session:
                await seed_auth_and_release(session)
                original = await session.scalar(select(ReleaseNote).where(ReleaseNote.version == first["app_version"]))
                original_id = original.id
                original_released_at = original.released_at

        with patch.multiple(settings, **second):
            async with SessionLocal() as session:
                await seed_auth_and_release(session)
                release = await session.scalar(select(ReleaseNote).where(ReleaseNote.version == second["app_version"]))
                current_count = await session.scalar(
                    select(func.count()).select_from(ReleaseNote).where(ReleaseNote.is_current.is_(True))
                )
                self.assertEqual(release.id, original_id)
                self.assertEqual(release.released_at, original_released_at)
                self.assertEqual(release.commit_sha, "new-commit")
                self.assertEqual(release.title, "Updated title")
                self.assertEqual(release.summary, "Updated summary")
                self.assertEqual(release.changes, ["updated", "verified"])
                self.assertTrue(release.is_current)
                self.assertEqual(current_count, 1)


if __name__ == "__main__":
    unittest.main()
