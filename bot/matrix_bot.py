import asyncio
import logging
from io import BytesIO
from zipfile import ZipFile
import tarfile

from nio import Event, MatrixRoomMessageEvent, MegolmEvent, RoomMessageFile, RoomMessageMedia, RoomMessageText

import main
from chat_transport import create_chat_transport, create_matrix_client, log_encrypted_event_once
from command_session import MatrixCommandSession
from notifications import Notifier
from timelapse import Timelapse
from websocket_helper import WebSocketHelper

logger = logging.getLogger(__name__)


async def matrix_greeting() -> None:
    if not main.configWrap.telegram_ui.send_greeting_message:
        await main.klippy.add_bot_announcements_feed()
        await matrix_check_unfinished_lapses()
        return
    response = await main.klippy.check_connection()
    mess = ""
    if response:
        mess += f"Bot online, no moonraker connection!\n {response} \nFailing..."
    else:
        mess += "Printer online on " + main.get_local_ip()
        if main.configWrap.configuration_errors:
            mess += await main.klippy.get_versions_info(bot_only=True) + main.configWrap.configuration_errors
    await main.chat_transport.send_text_message(mess, main.notifier.silent_status)
    await main.klippy.add_bot_announcements_feed()
    await matrix_check_unfinished_lapses()


async def matrix_check_unfinished_lapses() -> None:
    files = main.cameraWrap.detect_unfinished_lapses()
    if not files:
        return
    session = MatrixCommandSession(main.chat_transport)
    await session.reply_text(
        "Unfinished timelapses found:\n" + "\n".join(files) + "\nUse /lapse <name> to build or /lapse cleanup",
        silent=main.notifier.silent_status,
    )


async def matrix_lapse_command(args: str, session: MatrixCommandSession) -> None:
    if args.strip().lower() == "cleanup":
        main.cameraWrap.cleanup_unfinished_lapses()
        await session.reply_text("Removing unfinished timelapses data", silent=main.notifier.silent_commands)
        return
    if not args:
        await matrix_check_unfinished_lapses()
        return
    lapse_name = args.strip()
    info_mess = await main.chat_transport.send_text_message(f"Starting time-lapse assembly for {lapse_name}", main.notifier.silent_commands)
    await main.chat_transport.send_record_video()
    await main.timelapse.upload_timelapse(lapse_name, info_mess)
    await matrix_check_unfinished_lapses()


async def matrix_dispatch_command(body: str, session: MatrixCommandSession) -> None:
    text = body.strip()
    if not text:
        return

    if text.startswith("!") or text.startswith("/"):
        remainder = text[1:]
        parts = remainder.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
    elif text.upper() in main.klippy.macros_all:
        await main.ws_helper.execute_ws_gcode_script(text.upper())
        await session.reply_text(f"Running macro: {text.upper()}", silent=main.notifier.silent_commands)
        return
    elif text.endswith(".gcode"):
        if await main.klippy.start_printing_file(text):
            await session.reply_text(f"Started printing {text}", silent=main.notifier.silent_commands)
        else:
            await session.reply_text(f"Failed to start printing {text}", silent=main.notifier.silent_commands)
        return
    else:
        await session.reply_text(f"unknown command: {text}")
        return

    if cmd.upper() in main.klippy.macros_all:
        await main.ws_helper.execute_ws_gcode_script(cmd.upper())
        await session.reply_text(f"Running macro: {cmd.upper()}", silent=main.notifier.silent_commands)
        return

    if cmd in main.configWrap.telegram_ui.hidden_bot_commands:
        await session.reply_text(f"unknown command: {text}")
        return

    if cmd == "files" and args:
        if await main.klippy.start_printing_file(args.strip()):
            await session.reply_text(f"Started printing {args.strip()}", silent=main.notifier.silent_commands)
        else:
            await session.reply_text(f"Failed to start printing {args.strip()}", silent=main.notifier.silent_commands)
        return
    if cmd == "lapse":
        await matrix_lapse_command(args, session)
        return
    if cmd == "gcode":
        if args:
            await main.ws_helper.execute_ws_gcode_script(args)
        else:
            await session.reply_text("No command provided")
        return
    if cmd == "services" and args:
        await main.command_exec(session, f"Restarting service: {args.strip()}", main.ws_helper.restart_system_service(args.strip()))
        return

    command_map = {
        "help": main.help_command_no_confirm,
        "status": main.status_no_confirm,
        "ip": main.get_ip_no_confirm,
        "video": main.get_video_no_confirm,
        "pause": lambda s: main.command_exec(s, "Pausing printing", main.ws_helper.manage_printing("pause")),
        "resume": lambda s: main.command_exec(s, "Resuming printing", main.ws_helper.manage_printing("resume")),
        "cancel": lambda s: main.command_exec(s, "Canceling printing", main.ws_helper.manage_printing("cancel")),
        "power": main.power_toggle_no_confirm,
        "light": main.light_toggle_no_confirm,
        "emergency": lambda s: main.command_exec(s, "Executing emergency stop", main.ws_helper.emergency_stop_printer()),
        "shutdown": lambda s: main.command_exec(s, "Shutting down host", main.ws_helper.shutdown_pi_host()),
        "reboot": lambda s: main.command_exec(s, "Rebooting host", main.ws_helper.reboot_pi_host()),
        "bot_restart": lambda s: main.command_exec(s, "Restarting bot", main.restart_bot()),
        "fw_restart": lambda s: main.command_exec(s, "Restarting klipper firmware", main.ws_helper.firmware_restart_printer()),
        "services": main.services_keyboard_no_confirm,
        "files": main.get_gcode_files_no_confirm,
        "macros": main.get_macros_no_confirm,
        "logs": main.send_logs_no_confirm,
        "logs_upload": main.upload_logs_no_confirm,
    }
    if cmd in main.bot_commands() or cmd in command_map:
        handler = command_map.get(cmd)
        if handler:
            result = handler(session)
            if asyncio.iscoroutine(result):
                await result
            return
    await session.reply_text(f"unknown command: {text}")


async def matrix_process_upload(filename: str, file_bytes: bytes, session: MatrixCommandSession) -> None:
    if not filename.endswith((".gcode", ".zip", ".tar.gz", ".tar.bz2", ".tar.xz")):
        await session.reply_text(f"unknown filetype in {filename}", silent=main.notifier.silent_commands)
        return

    uploaded_bio = BytesIO(file_bytes)
    uploaded_bio.name = filename
    sending_bio = BytesIO()
    if filename.endswith(".gcode"):
        sending_bio = uploaded_bio
    elif filename.endswith(".zip"):
        with ZipFile(uploaded_bio) as my_zip_file:
            if len(my_zip_file.namelist()) > 1:
                await session.reply_text(f"Multiple files in archive {filename}", silent=main.notifier.silent_commands)
                return
            with my_zip_file.open(my_zip_file.namelist()[0]) as contained_file:
                sending_bio.name = contained_file.name
                sending_bio.write(contained_file.read())
                sending_bio.seek(0)
    elif filename.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(fileobj=uploaded_bio, mode="r:*") as tararch:
            if len(tararch.getmembers()) > 1:
                await session.reply_text(f"Multiple files in archive {filename}", silent=main.notifier.silent_commands)
                return
            archived_file = tararch.getmembers()[0]
            extracted_f = tararch.extractfile(archived_file)
            if extracted_f:
                sending_bio.name = archived_file.name
                sending_bio.write(extracted_f.read())
                sending_bio.seek(0)

    if sending_bio.name and sending_bio.name.endswith(".gcode"):
        if await main.klippy.upload_gcode_file(sending_bio, main.configWrap.bot_config.upload_path):
            path_name = f"{main.configWrap.bot_config.formatted_upload_path}{sending_bio.name}"
            mess, _thumb = await main.klippy.get_file_info_by_name(path_name, f"Successfully uploaded file:{path_name}")
            await session.reply_text(mess + f"\nStart print with /files {path_name}", silent=main.notifier.silent_commands)
            _thumb.close()
        else:
            await session.reply_text(f"Failed uploading file: {sending_bio.name}", silent=main.notifier.silent_commands)
    elif sending_bio.name:
        await session.reply_text(f"Not a gcode file {filename}", silent=main.notifier.silent_commands)

    uploaded_bio.close()
    sending_bio.close()


async def _matrix_message_callback(room: MatrixRoomMessageEvent, event: Event) -> None:
    if room.room_id != main.configWrap.bot_config.matrix_room:
        return
    if isinstance(event, MegolmEvent):
        log_encrypted_event_once()
        return
    if event.sender == main.configWrap.bot_config.matrix_user:
        return

    session = MatrixCommandSession(main.chat_transport)

    if isinstance(event, RoomMessageText):
        await matrix_dispatch_command(event.body, session)
        return

    if isinstance(event, (RoomMessageFile, RoomMessageMedia)):
        filename = getattr(event, "body", "upload.bin")
        response = await main.chat_transport._client.download(event.url)
        if hasattr(response, "body") and response.body:
            await matrix_process_upload(filename, response.body, session)
        else:
            await session.reply_text("Failed to download uploaded file", silent=main.notifier.silent_commands)


async def run_matrix_bot() -> None:
    client = create_matrix_client(main.configWrap)
    main.chat_transport = create_chat_transport(main.configWrap, matrix_client=client)
    main.timelapse = Timelapse(main.configWrap, main.klippy, main.cameraWrap, main.a_scheduler, main.chat_transport, main.rotating_handler)
    main.notifier = Notifier(main.configWrap, main.chat_transport, main.klippy, main.cameraWrap, main.a_scheduler, main.rotating_handler)
    main.ws_helper = WebSocketHelper(main.configWrap, main.klippy, main.notifier, main.timelapse, main.a_scheduler, main.rotating_handler)

    client.add_event_callback(_matrix_message_callback, MatrixRoomMessageEvent)

    main.a_scheduler.start()
    main.a_scheduler.add_job(matrix_greeting)
    loop = asyncio.get_event_loop()
    loop.create_task(main.ws_helper.run_forever_async())

    await client.sync_forever(timeout=30000, full_state=True)
