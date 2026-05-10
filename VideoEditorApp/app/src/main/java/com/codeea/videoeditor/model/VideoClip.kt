package com.codeea.videoeditor.model

import android.graphics.Bitmap
import android.net.Uri
import java.io.Serializable

data class VideoClip(
    val id: String,
    val uri: Uri,
    val name: String,
    val originalDurationMs: Long,
    var trimStartMs: Long = 0L,
    var trimEndMs: Long = originalDurationMs,
    var speedMultiplier: Float = 1.0f,
    var volume: Float = 1.0f,
    var rotation: Int = 0,
    var filter: VideoFilter = VideoFilter.NONE,
    var filterIntensity: Float = 0.8f,
    @Transient var thumbnails: List<Bitmap> = emptyList()
) : Serializable {
    val effectiveDurationMs: Long
        get() = ((trimEndMs - trimStartMs) / speedMultiplier).toLong()

    val displayDuration: String
        get() = formatMs(effectiveDurationMs)

    companion object {
        fun formatMs(ms: Long): String {
            val totalSeconds = ms / 1000
            val minutes = totalSeconds / 60
            val seconds = totalSeconds % 60
            return "%02d:%02d".format(minutes, seconds)
        }
    }
}
