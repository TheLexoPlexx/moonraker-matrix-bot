import logging
from abc import ABC, abstractmethod
from io import BytesIO
from typing import List, Optional, Tuple, Union

from configuration import ConfigWrapper
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown

logger = logging.getLogger(__name__)

_ENCRYPTED_LOGGED = False


class OutgoingMessage(ABC):
    @property
    @abstractmethod
    def has_caption(self) -> bool:
        ...

    @abstractmethod
    async def edit_text(self, text: str, parse_mode: Optional[str] = None) -> None:
        ...

    @abstractmethod
    async def edit_caption(self, caption: str, parse_mode: Optional[str] = None) -> None:
        ...

    @abstractmethod
    async def edit_media_photo(self, photo: BytesIO) -> None:
        ...

    @abstractmethod
    async def delete(self) -> None:
        ...


class TelegramOutgoingMessage(OutgoingMessage):
    def __init__(self, message: Message, bot: Bot, chat_id: int):
        self._message = message
        self._bot = bot
        self._chat_id = chat_id
        self.message_id = message.message_id

    @property
    def has_caption(self) -> bool:
        return self._message.caption is not None

    async def edit_text(self, text: str, parse_mode: Optional[str] = None) -> None:
        await self._message.edit_text(text=text, parse_mode=parse_mode or ParseMode.MARKDOWN_V2)

    async def edit_caption(self, caption: str, parse_mode: Optional[str] = None) -> None:
        await self._message.edit_caption(caption=caption, parse_mode=parse_mode or ParseMode.MARKDOWN_V2)

    async def edit_media_photo(self, photo: BytesIO) -> None:
        await self._message.edit_media(media=InputMediaPhoto(photo))

    async def delete(self) -> None:
        await self._bot.delete_message(self._chat_id, self.message_id)


class MatrixOutgoingMessage(OutgoingMessage):
    def __init__(self, transport: "MatrixChatTransport", event_id: str, has_media: bool = False):
        self._transport = transport
        self._event_id = event_id
        self._has_media = has_media

    @property
    def event_id(self) -> str:
        return self._event_id

    @property
    def has_caption(self) -> bool:
        return self._has_media

    async def edit_text(self, text: str, parse_mode: Optional[str] = None) -> None:
        await self._transport._replace_message(self._event_id, text, notice=False)

    async def edit_caption(self, caption: str, parse_mode: Optional[str] = None) -> None:
        await self._transport._replace_message(self._event_id, caption, notice=False)

    async def edit_media_photo(self, photo: BytesIO) -> None:
        await self._transport._replace_message_with_image(self._event_id, photo)

    async def delete(self) -> None:
        await self._transport._redact(self._event_id)


class ChatTransport(ABC):
    @abstractmethod
    def escape_progress(self, text: str) -> str:
        ...

    @abstractmethod
    async def send_typing(self) -> None:
        ...

    @abstractmethod
    async def send_upload_photo(self) -> None:
        ...

    @abstractmethod
    async def send_upload_document(self) -> None:
        ...

    @abstractmethod
    async def send_record_video(self) -> None:
        ...

    @abstractmethod
    async def send_text_message(self, text: str, silent: bool, parse_mode: Optional[str] = None) -> OutgoingMessage:
        ...

    @abstractmethod
    async def send_photo_message(self, photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        ...

    @abstractmethod
    async def send_video_message(
        self,
        video: Union[bytes, BytesIO],
        thumbnail: Optional[Union[bytes, BytesIO]],
        width: int,
        height: int,
        caption: str,
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        ...

    @abstractmethod
    async def send_media_group(
        self,
        media: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]],
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        ...

    @abstractmethod
    async def send_message_with_keyboard(self, text: str, keyboard: InlineKeyboardMarkup, silent: bool) -> None:
        ...

    @abstractmethod
    async def pin_status(self, message: OutgoingMessage) -> None:
        ...

    @abstractmethod
    async def unpin_all(self) -> None:
        ...

    @abstractmethod
    async def delete_message_id(self, message_id: int) -> None:
        ...

    @property
    @abstractmethod
    def supports_notify_groups(self) -> bool:
        ...

    @abstractmethod
    async def send_typing_group(self, group: int, message_thread_id: Optional[int]) -> None:
        ...

    @abstractmethod
    async def send_text_group(self, group: int, message_thread_id: Optional[int], text: str, silent: bool) -> OutgoingMessage:
        ...

    @abstractmethod
    async def send_photo_group(self, group: int, message_thread_id: Optional[int], photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        ...


class TelegramChatTransport(ChatTransport):
    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    def escape_progress(self, text: str) -> str:
        return escape_markdown(text, version=2)

    async def send_typing(self) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.TYPING)

    async def send_upload_photo(self) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.UPLOAD_PHOTO)

    async def send_upload_document(self) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    async def send_record_video(self) -> None:
        await self._bot.send_chat_action(chat_id=self._chat_id, action=ChatAction.RECORD_VIDEO)

    async def send_text_message(self, text: str, silent: bool, parse_mode: Optional[str] = None) -> OutgoingMessage:
        sent = await self._bot.send_message(
            self._chat_id,
            text=text,
            parse_mode=parse_mode or ParseMode.MARKDOWN_V2,
            disable_notification=silent,
        )
        return TelegramOutgoingMessage(sent, self._bot, self._chat_id)

    async def send_photo_message(self, photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        sent = await self._bot.send_photo(
            self._chat_id,
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=silent,
        )
        return TelegramOutgoingMessage(sent, self._bot, self._chat_id)

    async def send_video_message(
        self,
        video: Union[bytes, BytesIO],
        thumbnail: Optional[Union[bytes, BytesIO]],
        width: int,
        height: int,
        caption: str,
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        await self._bot.send_video(
            self._chat_id,
            video=video,
            thumbnail=thumbnail,
            width=width,
            height=height,
            caption=caption,
            write_timeout=write_timeout,
            disable_notification=silent,
        )

    async def send_media_group(
        self,
        media: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]],
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        await self._bot.send_media_group(self._chat_id, media=media, disable_notification=silent, write_timeout=write_timeout)

    async def send_message_with_keyboard(self, text: str, keyboard: InlineKeyboardMarkup, silent: bool) -> None:
        await self._bot.send_message(self._chat_id, text=text, reply_markup=keyboard, disable_notification=silent)

    async def pin_status(self, message: OutgoingMessage) -> None:
        if isinstance(message, TelegramOutgoingMessage):
            await self._bot.pin_chat_message(self._chat_id, message.message_id, disable_notification=True)

    async def unpin_all(self) -> None:
        await self._bot.unpin_all_chat_messages(self._chat_id)

    async def delete_message_id(self, message_id: int) -> None:
        try:
            await self._bot.delete_message(self._chat_id, message_id)
        except BadRequest as badreq:
            logger.warning("Failed deleting message \n%s", badreq)

    @property
    def supports_notify_groups(self) -> bool:
        return True

    async def send_typing_group(self, group: int, message_thread_id: Optional[int]) -> None:
        await self._bot.send_chat_action(chat_id=group, message_thread_id=message_thread_id, action=ChatAction.TYPING)

    async def send_text_group(self, group: int, message_thread_id: Optional[int], text: str, silent: bool) -> OutgoingMessage:
        sent = await self._bot.send_message(
            chat_id=group,
            message_thread_id=message_thread_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=silent,
        )
        return TelegramOutgoingMessage(sent, self._bot, group)

    async def send_photo_group(self, group: int, message_thread_id: Optional[int], photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        sent = await self._bot.send_photo(
            chat_id=group,
            message_thread_id=message_thread_id,
            photo=photo,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=silent,
        )
        return TelegramOutgoingMessage(sent, self._bot, group)


class MatrixChatTransport(ChatTransport):
    def __init__(self, client, room_id: str, max_upload_mb: int):
        self._client = client
        self._room_id = room_id
        self._max_upload = max_upload_mb * 1024 * 1024
        self._bzz_events: dict[int, str] = {}
        self._next_bzz = 1

    def escape_progress(self, text: str) -> str:
        return text

    def _msgtype(self, silent: bool) -> str:
        return "m.notice" if silent else "m.text"

    async def _send_room_text(self, body: str, silent: bool) -> str:
        from nio import RoomSendResponse

        resp = await self._client.room_send(
            self._room_id,
            "m.room.message",
            {"msgtype": self._msgtype(silent), "body": body},
        )
        if isinstance(resp, RoomSendResponse):
            return resp.event_id
        raise RuntimeError(f"Matrix send failed: {resp}")

    async def _replace_message(self, event_id: str, body: str, notice: bool = False) -> None:
        from nio import RoomSendResponse

        content = {
            "msgtype": "m.notice" if notice else "m.text",
            "body": body,
            "m.new_content": {"msgtype": "m.text", "body": body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        resp = await self._client.room_send(self._room_id, "m.room.message", content)
        if not isinstance(resp, RoomSendResponse):
            logger.warning("Matrix replace failed: %s", resp)

    async def _replace_message_with_image(self, event_id: str, photo: BytesIO) -> None:
        mxc, info = await self._upload_bytes(photo.getvalue(), photo.name or "photo.jpg", "image/jpeg")
        from nio import RoomSendResponse

        body = photo.name or "photo.jpg"
        content = {
            "msgtype": "m.image",
            "body": body,
            "url": mxc,
            "info": info,
            "m.new_content": {"msgtype": "m.image", "body": body, "url": mxc, "info": info},
            "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
        }
        resp = await self._client.room_send(self._room_id, "m.room.message", content)
        if not isinstance(resp, RoomSendResponse):
            logger.warning("Matrix replace image failed: %s", resp)

    async def _redact(self, event_id: str) -> None:
        await self._client.room_redact(self._room_id, event_id, reason="deleted")

    async def _upload_bytes(self, data: bytes, filename: str, content_type: str) -> Tuple[str, dict]:
        from nio import UploadResponse

        if len(data) > self._max_upload:
            raise ValueError(f"File exceeds upload limit ({self._max_upload // (1024 * 1024)}mb)")
        bio = BytesIO(data)
        resp, _decrypt = await self._client.upload(bio, filename=filename, content_type=content_type, filesize=len(data), encrypt=False)
        if not isinstance(resp, UploadResponse):
            raise RuntimeError(f"Matrix upload failed: {resp}")
        info = {"mimetype": content_type, "size": len(data)}
        return resp.content_uri, info

    async def send_typing(self) -> None:
        return None

    async def send_upload_photo(self) -> None:
        return None

    async def send_upload_document(self) -> None:
        return None

    async def send_record_video(self) -> None:
        return None

    async def send_text_message(self, text: str, silent: bool, parse_mode: Optional[str] = None) -> OutgoingMessage:
        event_id = await self._send_room_text(text, silent)
        return MatrixOutgoingMessage(self, event_id)

    async def send_photo_message(self, photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        mxc, info = await self._upload_bytes(photo.getvalue(), photo.name or "photo.jpg", "image/jpeg")
        from nio import RoomSendResponse

        content = {"msgtype": "m.image", "body": caption or (photo.name or "photo"), "url": mxc, "info": info}
        if caption:
            content["body"] = caption
        resp = await self._client.room_send(self._room_id, "m.room.message", content)
        if not isinstance(resp, RoomSendResponse):
            raise RuntimeError(f"Matrix image send failed: {resp}")
        return MatrixOutgoingMessage(self, resp.event_id, has_media=True)

    async def send_video_message(
        self,
        video: Union[bytes, BytesIO],
        thumbnail: Optional[Union[bytes, BytesIO]],
        width: int,
        height: int,
        caption: str,
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        if isinstance(video, BytesIO):
            video_bytes = video.getvalue()
        else:
            video_bytes = video
        mxc, info = await self._upload_bytes(video_bytes, "video.mp4", "video/mp4")
        info.update({"w": width, "h": height})
        from nio import RoomSendResponse

        content = {"msgtype": "m.video", "body": caption or "video", "url": mxc, "info": info}
        resp = await self._client.room_send(self._room_id, "m.room.message", content)
        if not isinstance(resp, RoomSendResponse):
            raise RuntimeError(f"Matrix video send failed: {resp}")

    async def send_media_group(
        self,
        media: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]],
        silent: bool,
        write_timeout: int = 120,
    ) -> None:
        for item in media:
            caption = getattr(item, "caption", None) or ""
            media_bytes = item.media
            filename = getattr(item, "filename", None) or "file"
            if isinstance(item, InputMediaPhoto):
                bio = BytesIO(media_bytes)
                bio.name = filename
                await self.send_photo_message(bio, caption, silent)
            elif isinstance(item, InputMediaVideo):
                bio = BytesIO(media_bytes)
                await self.send_video_message(bio, None, 0, 0, caption, silent, write_timeout)
            else:
                mxc, info = await self._upload_bytes(media_bytes, filename, "application/octet-stream")
                from nio import RoomSendResponse

                content = {"msgtype": "m.file", "body": caption or filename, "url": mxc, "info": info, "filename": filename}
                await self._client.room_send(self._room_id, "m.room.message", content)

    async def send_message_with_keyboard(self, text: str, keyboard: InlineKeyboardMarkup, silent: bool) -> None:
        lines = [text] if text else []
        for row in keyboard.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data != "do_nothing":
                    lines.append(f"{btn.text}: {btn.callback_data}")
                elif btn.text:
                    lines.append(btn.text)
        await self.send_text_message("\n".join(lines), silent)

    async def pin_status(self, message: OutgoingMessage) -> None:
        return None

    async def unpin_all(self) -> None:
        return None

    async def delete_message_id(self, message_id: int) -> None:
        event_id = self._bzz_events.pop(message_id, None)
        if event_id:
            await self._redact(event_id)

    def register_bzz_event(self, event_id: str) -> int:
        bzz_id = self._next_bzz
        self._next_bzz += 1
        self._bzz_events[bzz_id] = event_id
        return bzz_id

    @property
    def supports_notify_groups(self) -> bool:
        return False

    async def send_typing_group(self, group: int, message_thread_id: Optional[int]) -> None:
        return None

    async def send_text_group(self, group: int, message_thread_id: Optional[int], text: str, silent: bool) -> OutgoingMessage:
        return await self.send_text_message(text, silent)

    async def send_photo_group(self, group: int, message_thread_id: Optional[int], photo: BytesIO, caption: str, silent: bool) -> OutgoingMessage:
        return await self.send_photo_message(photo, caption, silent)


def create_matrix_client(config: ConfigWrapper):
    from nio import AsyncClient

    client = AsyncClient(config.bot_config.homeserver, config.bot_config.matrix_user, device_id="moonraker-matrix-bot")
    client.access_token = config.secrets.matrix_token
    return client


def create_chat_transport(config: ConfigWrapper, bot: Optional[Bot] = None, matrix_client=None) -> ChatTransport:
    if config.bot_config.platform == "matrix":
        return MatrixChatTransport(matrix_client, config.bot_config.matrix_room, config.bot_config.max_upload_file_size)
    if bot is None:
        raise ValueError("Telegram transport requires a bot instance")
    return TelegramChatTransport(bot, config.secrets.chat_id)


def log_encrypted_event_once() -> None:
    global _ENCRYPTED_LOGGED
    if not _ENCRYPTED_LOGGED:
        logger.warning("Encrypted Matrix events are ignored; use an unencrypted room")
        _ENCRYPTED_LOGGED = True
