"""Key-value system settings stored in the database."""

from sqlalchemy import Column, String, Text
from app.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False, default="")
