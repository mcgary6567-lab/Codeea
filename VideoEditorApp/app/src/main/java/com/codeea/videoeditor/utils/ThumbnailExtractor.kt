package com.codeea.videoeditor.utils

import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

object ThumbnailExtractor {

    suspend fun extractThumbnails(
        context: Context,
        uri: Uri,
        count: Int,
        widthPx: Int = 112,
        heightPx: Int = 112
    ): List<Bitmap> = withContext(Dispatchers.IO) {
        val retriever = MediaMetadataRetriever()
        val thumbnails = mutableListOf<Bitmap>()
        try {
            retriever.setDataSource(context, uri)
            val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull() ?: return@withContext emptyList()

            val intervalUs = (durationMs * 1000L) / maxOf(count, 1)
            for (i in 0 until count) {
                val timeUs = i * intervalUs
                val bitmap = retriever.getScaledFrameAtTime(
                    timeUs,
                    MediaMetadataRetriever.OPTION_CLOSEST_SYNC,
                    widthPx,
                    heightPx
                )
                if (bitmap != null) thumbnails.add(bitmap)
            }
        } catch (_: Exception) {
        } finally {
            retriever.release()
        }
        thumbnails
    }

    suspend fun extractSingleThumbnail(
        context: Context,
        uri: Uri,
        timeMs: Long = 0L,
        widthPx: Int = 192,
        heightPx: Int = 108
    ): Bitmap? = withContext(Dispatchers.IO) {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(context, uri)
            retriever.getScaledFrameAtTime(
                timeMs * 1000L,
                MediaMetadataRetriever.OPTION_CLOSEST_SYNC,
                widthPx,
                heightPx
            )
        } catch (_: Exception) {
            null
        } finally {
            retriever.release()
        }
    }
}
