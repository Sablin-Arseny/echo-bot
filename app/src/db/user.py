from functools import cache

from sqlalchemy import select

from app.src.db.core import BaseDB
from app.src.models import User as UserOrm
from app.src.schemas import User


class UserDB(BaseDB):
    """Выполняет операции с пользователями в базе.

    Создаёт, читает и обновляет пользовательские записи.
    """

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls()

    async def create(self, user: User):
        """Создаёт новую запись пользователя.

        Сохраняет данные схемы и возвращает ORM-объект.
        """

        async with self.create_session() as session:
            user = UserOrm(**user.model_dump(exclude_none=True))
            session.add(user)

        async with self.create_session() as session:
            await session.get(UserOrm, user.id)
            return user

    async def update(self, user: User, update_user: User):
        """Обновляет существующую запись пользователя.

        Изменяет заполненные поля, кроме идентификатора.
        """

        async with self.create_session() as session:
            user_orm = await session.get(UserOrm, user.id)
            update_data = update_user.model_dump(exclude_none=True)

            for key, value in update_data.items():
                if key == "id":
                    raise KeyError("it is forbidden to change the id")
                else:
                    setattr(user_orm, key, value)

            await session.commit()
            await session.refresh(user_orm)
            return user_orm

    async def get(self, user: User):
        """Находит пользователя по заполненным полям.

        Объединяет переданные значения точными условиями.
        """

        stmt = select(UserOrm)
        for key, value in user.model_dump(exclude_none=True).items():
            stmt = stmt.where(getattr(UserOrm, key) == value)

        async with self.create_session() as session:
            user = await session.scalar(stmt)
            return user

    async def get_all(self):
        """Возвращает все записи пользователей.

        Результат содержит список ORM-объектов.
        """

        stmt = select(UserOrm)

        async with self.create_session() as session:
            users = await session.scalars(stmt)
            return users.all()

    async def check_user(self, user_dict: dict) -> bool:
        """Проверяет существование пользователя по полям.

        Учитывает известные непустые поля словаря.
        """

        stmt = select(UserOrm)
        for key, value in user_dict.items():
            if hasattr(UserOrm, key) and value is not None:
                stmt = stmt.where(getattr(UserOrm, key) == value)
        async with self.create_session() as session:
            user = await session.scalar(stmt)
            return user is not None
