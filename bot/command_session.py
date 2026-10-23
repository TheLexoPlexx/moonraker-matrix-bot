import re
from abc import ABC, abstractmethod
from io import BytesIO
from typing import List, Optional, Union

from telegram import InlineKeyboardMarkup, InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message
from telegram.constants import ChatAction, ParseMode

from chat_transport import ChatTransport, TelegramChatTransport


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


class ReplyHandle(ABC):
    @abstractmethod
    async def edit_text(self, text: str, parse_mode: Optional[str] = ParseMode.HTML) -> None:
        ...

    async def delete(self) -> None:
        return None


class TelegramReplyHandle(ReplyHandle):
    def __init__(self, message: Message):
        self._message = message

    async def edit_text(self, text: str, parse_mode: Optional[str] = ParseMode.HTML) -> None:
        await self._message.edit_text(text, parse_mode=parse_mode)

    async def delete(self) -> None:
        await self._message.delete()


class MatrixReplyHandle(ReplyHandle):
    def __init__(self, outgoing):
        self._outgoing = outgoing

    async def edit_text(self, text: str, parse_mode: Optional[str] = ParseMode.HTML) -> None:
        await self._outgoing.edit_text(text)


class CommandSession(ABC):
    @abstractmethod
    async def send_chat_action_typing(self) -> None:
        ...

    @abstractmethod
    async def send_chat_action_upload_photo(self) -> None:
        ...

    @abstractmethod
    async def send_chat_action_upload_document(self) -> None:
        ...

    @abstractmethod
    async def send_chat_action_record_video(self) -> None:
        ...

    @abstractmethod
    async def reply_text(
        self,
        text: str,
        parse_mode: Optional[str] = ParseMode.HTML,
        quote: bool = True,
        silent: bool = False,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> ReplyHandle:
        ...

    @abstractmethod
    async def reply_photo(
        self,
        photo: BytesIO,
        caption: str,
        parse_mode: Optional[str] = ParseMode.HTML,
        silent: bool = False,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        quote: bool = True,
    ) -> None:
        ...

    @abstractmethod
    async def reply_video(
        self,
        video: BytesIO,
        thumbnail: BytesIO,
        width: int,
        height: int,
        caption: str,
        silent: bool = False,
        quote: bool = True,
        write_timeout: int = 120,
    ) -> None:
        ...

    @abstractmethod
    async def reply_media_group(
        self,
        media: List[Union[InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo]],
        silent: bool = False,
        quote: bool = True,
        write_timeout: int = 120,
    ) -> None:
        ...

    @abstractmethod
    async def delete(self) -> None:
        ...


class TelegramCommandSession(CommandSession):
    def __init__(self, message: Message):
        self._message = message

    @property
    def telegram_message(self) -> Message:
        return self._message

    async def send_chat_action_typing(self) -> None:
        if self._message.get_bot() is not None:
            await self._message.get_bot().send_chat_action(chat_id=self._message.chat_id, action=ChatAction.TYPING)

    async def send_chat_action_upload_photo(self) -> None:
        if self._message.get_bot() is not None:
            await self._message.get_bot().send_chat_action(chat_id=self._message.chat_id, action=ChatAction.UPLOAD_PHOTO)

    async def send_chat_action_upload_document(self) -> None:
        if self._message.get_bot() is not None:
            await self._message.get_bot().send_chat_action(chat_id=self._message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    async def send_chat_action_record_video(self) -> None:
        if self._message.get_bot() is not None:
            await self._message.get_bot().send_chat_action(chat_id=self._message.chat_id, action=ChatAction.RECORD_VIDEO)

    async def reply_text(self, text, parse_mode=ParseMode.HTML, quote=True, silent=False, reply_markup=None) -> ReplyHandle:
        sent = await self._message.reply_text(
            text,
            parse_mode=parse_mode,
            disable_notification=silent,
            quote=quote,
            reply_markup=reply_markup,
        )
        return TelegramReplyHandle(sent)

    async def reply_photo(self, photo, caption, parse_mode=ParseMode.HTML, silent=False, reply_markup=None, quote=True):
        await self._message.reply_photo(
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            disable_notification=silent,
            reply_markup=reply_markup,
            quote=quote,
        )

    async def reply_video(self, video, thumbnail, width, height, caption, silent=False, quote=True, write_timeout=120):
        await self._message.reply_video(
            video=video,
            thumbnail=thumbnail,
            width=width,
            height=height,
            caption=caption,
            disable_notification=silent,
            quote=quote,
            write_timeout=write_timeout,
        )

    async def reply_media_group(self, media, silent=False, quote=True, write_timeout=120):
        await self._message.reply_media_group(media, disable_notification=silent, quote=quote, write_timeout=write_timeout)

    async def delete(self) -> None:
        await self._message.delete()


class MatrixCommandSession(CommandSession):
    def __init__(self, transport: ChatTransport):
        self._transport = transport

    def _text(self, text: str, parse_mode: Optional[str]) -> str:
        if parse_mode == ParseMode.HTML:
            return _strip_html(text)
        return text

    async def send_chat_action_typing(self) -> None:
        await self._transport.send_typing()

    async def send_chat_action_upload_photo(self) -> None:
        await self._transport.send_upload_photo()

    async def send_chat_action_upload_document(self) -> None:
        await self._transport.send_upload_document()

    async def send_chat_action_record_video(self) -> None:
        await self._transport.send_record_video()

    async def reply_text(self, text, parse_mode=ParseMode.HTML, quote=True, silent=False, reply_markup=None) -> ReplyHandle:
        if reply_markup is not None:
            await self._transport.send_message_with_keyboard(self._text(text, parse_mode), reply_markup, silent)
            outgoing = await self._transport.send_text_message(self._text(text, parse_mode), silent)
        else:
            outgoing = await self._transport.send_text_message(self._text(text, parse_mode), silent)
        return MatrixReplyHandle(outgoing)

    async def reply_photo(self, photo, caption, parse_mode=ParseMode.HTML, silent=False, reply_markup=None, quote=True):
        if reply_markup is not None:
            await self._transport.send_photo_message(photo, self._text(caption, parse_mode), silent)
            await self._transport.send_message_with_keyboard("", reply_markup, silent)
        else:
            await self._transport.send_photo_message(photo, self._text(caption, parse_mode), silent)

    async def reply_video(self, video, thumbnail, width, height, caption, silent=False, quote=True, write_timeout=120):
        await self._transport.send_video_message(video, thumbnail, width, height, self._text(caption, parse_mode), silent, write_timeout)

    async def reply_media_group(self, media, silent=False, quote=True, write_timeout=120):
        await self._transport.send_media_group(media, silent, write_timeout)

    async def delete(self) -> None:
        return None
