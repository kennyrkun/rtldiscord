import sys
import discord
import samplerate
import pyaudio
import numpy as np
import subprocess
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from discord.ext import commands

sdrProcess = None

class PCMAudioPlayer(discord.AudioSource):
    def __init__(self, device, audio) -> None:
        super().__init__()

        self.ratio = 48000 / device["defaultSampleRate"]
        self.channels = device["maxInputChannels"]
        self.chunk = int(device["defaultSampleRate"] * 0.02)
        self.stream = audio.open(
            format             = pyaudio.paInt16,
            channels           = self.channels,
            rate               = int(device["defaultSampleRate"]),
            input              = True,
            input_device_index = device["index"],
            frames_per_buffer  = self.chunk,
        )

        if self.ratio != 1:
            logger.info("using samplerate")
            self.resampler = samplerate.Resampler("sinc_best", channels=2)
        else:
            logger.info("NOT using samplerate")
            self.resampler = None

    def read(self) -> bytes:
        frame = self.stream.read(self.chunk, exception_on_overflow=False)
        frame = np.frombuffer(frame, dtype=np.int16)

        frame = frame * (80 / 100)

        if self.channels == 1:
            frame = np.repeat(frame, 2)

        if self.resampler:
            frame = np.stack((frame[::2], frame[1::2]) , axis=1)
            return self.resampler.process(frame, self.ratio).astype(np.int16).tobytes()

        return frame.tobytes()

    def __del__(self):
        logger.info("destroying PCMAudioPlayer")
        self.stream.close()

def createBot(commandPrefix, device, audio) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(
        command_prefix = commandPrefix,
        description = "SDR Audio Streamer and goofy lil guy!",
        intents = intents
    )

    async def killSDRProcess():
        global sdrProcess

        if sdrProcess is None:
            return

        logger.info("Shutting down sdrSubProcess...")
        sdrProcess.kill()
        outs, errs = sdrProcess.communicate()

    async def shutdown(ctx = None):
        await killSDRProcess()

        if ctx is not None:
            await ctx.voice_client.disconnect()
            await ct.xbot.logout()

        exit()

    @bot.event
    async def on_ready():
        logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')

        await bot.change_presence(
            activity = discord.Activity(
                type = discord.ActivityType.listening,
                name = "the airwaves",
                url = "https://www.rtl-sdr.com/"
            )
        )

    @bot.command(name="play", help="Play audio")
    async def play(ctx):
        if ctx.author.voice is None:
            await ctx.send("You must be connected a voice channel.")
            raise commands.CommandError("Author not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.voice_client.is_playing():
            ctx.voice_client.stop()

        await ctx.author.voice.channel.connect()

        global sdrProcess

        if sdrProcess is not None:
            killSDRProcess()

        await ctx.send(f"Starting OP25 for OKWIN...")

        sdrProcess = await asyncio.create_subprocess_exec(
            "/home/sdr/op25/op25/gr-op25_repeater/apps/rx.py",
                "--trunk-conf-file", "okwin.tsv",
                "--freq-error-tracking",
                "--nocrypt",
                "--vocoder",
                "--phase2-tdma",
                "--args", "rtl",
                "--gains", "lna:36",
                "--sample-rate", "960000",
                "--fine-tune", "500", # fine tune frequency offset
                "--freq-corr", "0",
                "--verbosity", "0",
                "--demod-type", "cqpsk",
                "--terminal-type", "http:192.168.0.9:8080",
                "--udp-player",
                "--audio-output", "hw:2,1",
            stdout = asyncio.subprocess.PIPE,
            stderr = asyncio.subprocess.STDOUT,
            cwd = "/home/sdr/op25/op25/gr-op25_repeater/apps/"
        )

        await ctx.send(f"Waiting for NAC...")

        while sdrProcess.returncode is None:
            line = await sdrProcess.stdout.readline()

            if not line:
                continue

            line = line.decode()
            logger.info(line)

            if line.find("Reconfiguring NAC") != -1:
                await ctx.send("NAC acquired, beginning streaming...")
                break

        ctx.voice_client.play(PCMAudioPlayer(device, audio), after=lambda e: print(f'Player error: {e}') if e else None)

        await ctx.send(f"Streaming OKWIN.")

        await bot.change_presence(activity = 
            discord.Streaming(
                name = "OKWIN",
                url = "https://github.com/boatbod/op25"
            )
        )

    # @play.on_command_error
    # async def playFail(ctx):
    #     await ctx.send("Play what? `okwin` or `cps`")

    @bot.command(name="stop", help="Disconnect bot")
    async def stop(ctx):
        await shutdown(ctx)

    @bot.event
    async def on_voice_state_update(member, before, after):
        if before.channel is None:
            return

        if len(before.channel.members) - 1 < 1:
            await shutdown()

    return bot

#discord.opus.load_opus("/opt/homebrew/lib/libopus.dylib")
#if not discord.opus.is_loaded():
#    raise RunTimeError('Opus failed to load')

if __name__ == "__main__":
    try:
        with open("token.txt", 'r') as f:
            token = f.readline()
    except FileNotFoundError:
        print("Token file not found!")
        input("Press Enter to exit")
        sys.exit(1)

    p = pyaudio.PyAudio()

    deviceInfo = p.get_device_info_by_index(1)

    logger.info(f'chosen device: {deviceInfo["name"]}, samplerate: {deviceInfo["defaultSampleRate"]}, Channels: {deviceInfo["maxInputChannels"]}')

    # start bot
    createBot("!", deviceInfo, p).run(token)
