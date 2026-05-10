package com.codeea.videoeditor.model

data class ExportConfig(
    var resolution: Resolution = Resolution.R1080P,
    var quality: Quality = Quality.MEDIUM,
    var fps: Int = 30
) {
    enum class Resolution(val label: String, val width: Int, val height: Int, val bitrate: Int) {
        R720P("720p", 1280, 720, 4_000_000),
        R1080P("1080p", 1920, 1080, 8_000_000),
        R4K("4K", 3840, 2160, 35_000_000)
    }

    enum class Quality(val label: String, val bitrateMultiplier: Float) {
        LOW("Low", 0.5f),
        MEDIUM("Medium", 1.0f),
        HIGH("High", 1.8f)
    }

    val videoBitrate: Int
        get() = (resolution.bitrate * quality.bitrateMultiplier).toInt()

    val audioBitrate: Int = 128_000

    fun estimatedSizeMb(durationSeconds: Long): Long {
        val videoBytes = videoBitrate.toLong() * durationSeconds / 8
        val audioBytes = audioBitrate.toLong() * durationSeconds / 8
        return (videoBytes + audioBytes) / 1_048_576
    }
}
