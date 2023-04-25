import argparse
import contextlib
import gzip
import json
import sys
from pathlib import Path
from typing import TextIO

import sqlalchemy
import sqlmodel
from fastapi.encoders import jsonable_encoder
from oasst_data import ExportMessageNode, ExportMessageTree
from oasst_inference_server.models import DbChat, DbMessage
from oasst_inference_server.settings import settings
from sqlmodel import Session


@contextlib.contextmanager
def create_session():
    engine = sqlmodel.create_engine(
        settings.database_uri,
        echo=settings.db_echo,
        isolation_level="REPEATABLE READ",
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )

    with Session(engine) as db:
        yield db


# see https://stackoverflow.com/questions/17602878/how-to-handle-both-with-open-and-sys-stdout-nicely
@contextlib.contextmanager
def smart_open(filename: str = None) -> TextIO:
    if filename and filename != "-":
        fh = open(filename, "wt", encoding="UTF-8")
    else:
        fh = sys.stdout

    try:
        yield fh
    finally:
        if fh is not sys.stdout:
            fh.close()


def prepare_export_message_tree(chat: DbChat) -> ExportMessageTree:
    # TODO
    pass


def prepare_export_message_node(message: DbMessage) -> ExportMessageNode:
    if message.worker_config:
        model_name = message.worker_config.model_config.model_id
    else:
        model_name = None

    # Chat prompts are human-written, responses are synthetic
    synthetic = message.role == "assistant"

    return ExportMessageNode(
        message_id=str(message.id),
        parent_id=str(message.parent_id),
        user_id=str(message.chat.user_id),
        created_date=message.created_at,
        text=message.content,
        role=message.role,
        synthetic=synthetic,
        model_name=model_name,
        # TODO: include reports
        # TODO: include thumbs up/down rating somehow?
    )


def write_messages_to_file(
    file: Path,
    chats: list[DbChat],
    use_compression: bool = True,
    write_trees: bool = True,
) -> None:
    out_buff: TextIO

    if use_compression:
        if not file:
            raise RuntimeError("File name must be specified when using compression.")
        out_buff = gzip.open(file, "wt", encoding="UTF-8")
    else:
        out_buff = smart_open(file)

    with out_buff as f:
        for c in chats:
            if write_trees:
                export_chat = prepare_export_message_tree(c)
                file_data = jsonable_encoder(export_chat, exclude_none=True)
                json.dump(file_data, f)
                f.write("\n")
            else:
                for m in c.messages:
                    export_message = prepare_export_message_node(m)
                    file_data = jsonable_encoder(export_message, exclude_none=True)
                    json.dump(file_data, f)
                    f.write("\n")


def fetch_eligible_chats(session: Session) -> list[DbChat]:
    """Fetch chats which are not opted out of data collection."""
    query = (
        session.query(DbChat)
        .filter(DbChat.allow_data_use)
        .options(
            sqlalchemy.orm.selectinload(DbChat.messages).selectinload(DbMessage.reports),
        )
    )
    chats: list[DbChat] = query.all()
    return chats


def export_chats(
    session: Session,
    export_path: Path,
    use_compression: bool = True,
    write_trees: bool = True,
) -> None:
    eligible_chats: list[DbChat] = fetch_eligible_chats(session)

    write_messages_to_file(
        export_path,
        eligible_chats,
        write_trees=write_trees,
        use_compression=use_compression,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--export-file",
        type=str,
        help="Name of file to export chats to. If not provided, output will be sent to STDOUT",
    )
    parser.add_argument(
        "--use-compression",
        action="store_false",
        help="Whether to use compression when writing to file. Defaults to True.",
    )
    parser.add_argument(
        "--write-trees",
        action="store_false",
        help="Whether to write chats as trees rather than individual messages. Defaults to True.",
    )
    # TODO: filters: reported, thumbs up/downed, specific user ID, specific chat ID, lang, etc
    # TODO: limit
    # TODO: user ID anonymization
    # TODO: date range?
    return parser.parse_args()


def main():
    args = parse_args()

    export_path = Path(args.export_file) if args.export_file else None

    with create_session() as session:
        export_chats(
            session,
            export_path,
            use_compression=args.use_compression,
            write_trees=args.write_trees,
        )


if __name__ == "__main__":
    main()
