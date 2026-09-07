import asyncio

from textual.app import App, ComposeResult

from miniclaude.cli.tui.widgets import EventStream


class CommandRenderingApp(App):
    def compose(self) -> ComposeResult:
        yield EventStream(id="events")


def test_command_result_renders_human_message_not_json_payload():
    async def scenario():
        app = CommandRenderingApp()
        async with app.run_test() as pilot:
            stream = app.query_one(EventStream)
            stream.append_event(
                {
                    "type": "command_result",
                    "command": "/help",
                    "ok": True,
                    "message": "Available commands:\n/help  Show help\n/status  Show status",
                }
            )
            await pilot.pause()

            rendered = app.query_one(".command-card").render().plain
            assert rendered == (
                "Command · /help\n"
                "Available commands:\n"
                "/help  Show help\n"
                "/status  Show status"
            )
            assert '"message"' not in rendered
            assert "\\n" not in rendered

    asyncio.run(scenario())


def test_failed_command_uses_same_readable_shape():
    async def scenario():
        app = CommandRenderingApp()
        async with app.run_test() as pilot:
            stream = app.query_one(EventStream)
            stream.append_event(
                {
                    "type": "command_result",
                    "command": "/missing",
                    "ok": False,
                    "message": "Unknown command. Use /help.",
                }
            )
            await pilot.pause()

            rendered = app.query_one(".command-card").render().plain
            assert rendered == "Command failed · /missing\nUnknown command. Use /help."

    asyncio.run(scenario())
