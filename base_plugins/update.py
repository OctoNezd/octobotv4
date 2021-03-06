import html
import os
import subprocess

import telegram

import octobot

inf = octobot.PluginInfo("Bot update",
                         handler_kwargs={
                             "CommandHandler": {
                                 "prefix": "sys!",
                                 "hidden": True,
                                 "inline_support": False
                             }
                         },
                         reply_kwargs={"editable": False})

if octobot.is_docker:
    inf.state = octobot.PluginStates.disabled
    inf.state_description = "Running inside Docker"

def reload(bot: octobot.OctoBot, ctx):
    msg: telegram.Message = ctx.reply("Reloading...")
    msg_res = []
    for plugin in bot.discover_plugins()["load_order"]:
        res = bot.load_plugin(plugin)
        msg_res.append(f"{plugin} - {res}")
    bot.update_handlers()
    ctx.edit("Reload complete. Plugin statuses:\n" + "\n".join(msg_res))


@octobot.CommandHandler("reload")
@octobot.permissions("is_bot_owner")
def reload_cmd(bot, ctx):
    reload_type = "soft"
    if len(ctx.args) > 0:
        reload_type = ctx.args[0]
    if reload_type == "soft":
        reload(bot, ctx)
    else:
        bot.stop()


@octobot.CommandHandler("update")
@octobot.permissions("is_bot_owner")
def update(bot, ctx):
    update_type = "soft"
    if len(ctx.args) > 0:
        update_type = ctx.args[0]
    update_type = update_type.lower()
    if update_type not in ["soft", "hard"]:
        ctx.reply(f"unknown update type {update_type}")
        return
    pull_res = subprocess.check_output("git pull", shell=True)
    ctx.reply(f"<code>{html.escape(pull_res.decode())}</code>", parse_mode="HTML")
    if update_type == "soft":
        reload(bot, ctx)
    else:
        bot.stop()
