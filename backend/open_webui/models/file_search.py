import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text

from open_webui.internal.db import Base, get_db


class FileSearchCredential(Base):
    __tablename__ = "file_search_credential"

    user_id = Column(Text, primary_key=True, unique=True)
    username = Column(Text, nullable=False)
    encrypted_password = Column(Text, nullable=False)
    default_directory = Column(Text, nullable=False, default="")
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class FileSearchCredentialModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str
    encrypted_password: str
    default_directory: str = ""
    created_at: int
    updated_at: int


class FileSearchCredentialsTable:
    def get_by_user_id(self, user_id: str) -> Optional[FileSearchCredentialModel]:
        with get_db() as db:
            credential = (
                db.query(FileSearchCredential)
                .filter(FileSearchCredential.user_id == user_id)
                .first()
            )
            return (
                FileSearchCredentialModel.model_validate(credential)
                if credential
                else None
            )

    def upsert(
        self,
        user_id: str,
        username: str,
        encrypted_password: str,
        default_directory: str = "",
    ) -> FileSearchCredentialModel:
        now = int(time.time_ns())
        with get_db() as db:
            credential = (
                db.query(FileSearchCredential)
                .filter(FileSearchCredential.user_id == user_id)
                .first()
            )
            if credential:
                credential.username = username
                credential.encrypted_password = encrypted_password
                credential.default_directory = default_directory
                credential.updated_at = now
            else:
                credential = FileSearchCredential(
                    user_id=user_id,
                    username=username,
                    encrypted_password=encrypted_password,
                    default_directory=default_directory,
                    created_at=now,
                    updated_at=now,
                )
                db.add(credential)
            db.commit()
            db.refresh(credential)
            return FileSearchCredentialModel.model_validate(credential)

    def delete_by_user_id(self, user_id: str) -> bool:
        with get_db() as db:
            deleted = (
                db.query(FileSearchCredential)
                .filter(FileSearchCredential.user_id == user_id)
                .delete()
            )
            db.commit()
            return bool(deleted)


FileSearchCredentials = FileSearchCredentialsTable()
