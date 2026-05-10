package com.codeea.videoeditor.model

import java.io.Serializable
import java.util.Date

data class EditProject(
    val id: String,
    var name: String,
    var clips: MutableList<VideoClip> = mutableListOf(),
    var audioTracks: MutableList<AudioTrack> = mutableListOf(),
    var textOverlays: MutableList<TextOverlay> = mutableListOf(),
    var originalVideoVolume: Float = 1.0f,
    val createdAt: Date = Date(),
    var updatedAt: Date = Date(),
    var thumbnailPath: String? = null
) : Serializable {
    val totalDurationMs: Long
        get() = clips.sumOf { it.effectiveDurationMs }

    val displayDuration: String
        get() = VideoClip.formatMs(totalDurationMs)

    fun isNotEmpty(): Boolean = clips.isNotEmpty()
}
