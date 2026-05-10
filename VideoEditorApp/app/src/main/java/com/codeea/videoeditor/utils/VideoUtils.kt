package com.codeea.videoeditor.utils

import android.content.ContentValues
import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object VideoUtils {

    fun getVideoDurationMs(context: Context, uri: Uri): Long {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
        } catch (e: Exception) {
            0L
        } finally {
            retriever.release()
        }
    }

    fun getVideoWidth(context: Context, uri: Uri): Int {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull() ?: 0
        } catch (e: Exception) {
            0
        } finally {
            retriever.release()
        }
    }

    fun getVideoHeight(context: Context, uri: Uri): Int {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull() ?: 0
        } catch (e: Exception) {
            0
        } finally {
            retriever.release()
        }
    }

    fun getVideoName(context: Context, uri: Uri): String {
        var name = "video_${System.currentTimeMillis()}"
        try {
            context.contentResolver.query(uri, arrayOf(MediaStore.Video.Media.DISPLAY_NAME), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    val nameIndex = cursor.getColumnIndex(MediaStore.Video.Media.DISPLAY_NAME)
                    if (nameIndex >= 0) name = cursor.getString(nameIndex)
                }
            }
        } catch (_: Exception) {}
        return name.substringBeforeLast(".")
    }

    fun createOutputFile(context: Context, prefix: String = "codeea_export"): File {
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val fileName = "${prefix}_$timestamp.mp4"
        val dir = context.getExternalFilesDir(Environment.DIRECTORY_MOVIES)
            ?: context.filesDir
        return File(dir, fileName)
    }

    fun saveVideoToGallery(context: Context, videoFile: File): Uri? {
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Video.Media.DISPLAY_NAME, videoFile.name)
                    put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
                    put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES + "/Codeea")
                    put(MediaStore.Video.Media.IS_PENDING, 1)
                }
                val uri = context.contentResolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
                uri?.also { insertUri ->
                    context.contentResolver.openOutputStream(insertUri)?.use { outputStream ->
                        videoFile.inputStream().use { it.copyTo(outputStream) }
                    }
                    values.clear()
                    values.put(MediaStore.Video.Media.IS_PENDING, 0)
                    context.contentResolver.update(insertUri, values, null, null)
                }
            } else {
                Uri.fromFile(videoFile)
            }
        } catch (e: Exception) {
            null
        }
    }

    fun formatDuration(ms: Long): String {
        val totalSeconds = ms / 1000
        val hours = totalSeconds / 3600
        val minutes = (totalSeconds % 3600) / 60
        val seconds = totalSeconds % 60
        return if (hours > 0) {
            "%d:%02d:%02d".format(hours, minutes, seconds)
        } else {
            "%02d:%02d".format(minutes, seconds)
        }
    }
}
