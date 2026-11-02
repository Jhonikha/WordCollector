package com.smartvocab.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python

class ClipboardMonitorService : Service() {

    private lateinit var clipboardManager: ClipboardManager
    private var lastClipboardText: String = ""
    private val CHANNEL_ID = "clipboard_monitor"
    private val NOTIFICATION_ID = 1

    private val clipboardListener = ClipboardManager.OnPrimaryClipChangedListener {
        val clip = clipboardManager.primaryClip
        val text = clip?.getItemAt(0)?.text?.toString() ?: return@OnPrimaryClipChangedListener

        if (text != lastClipboardText && text.isNotBlank()) {
            lastClipboardText = text

            Thread {
                try {
                    val py = Python.getInstance()
                    val mainModule = py.getModule("main")
                    val result = mainModule.callAttr("add_words_from_clipboard", text)
                    updateNotification("Added: ${text.take(30)}...")
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }.start()
        }
    }

    override fun onCreate() {
        super.onCreate()

        clipboardManager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboardManager.addPrimaryClipChangedListener(clipboardListener)

        startForeground(NOTIFICATION_ID, createNotification("Monitoring clipboard..."))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        clipboardManager.removePrimaryClipChangedListener(clipboardListener)
    }

    private fun createNotification(content: String): Notification {
        createNotificationChannel()

        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Smart Vocab Builder")
            .setContentText(content)
            .setSmallIcon(android.R.drawable.ic_menu_edit)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(content: String) {
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(NOTIFICATION_ID, createNotification(content))
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Clipboard Monitor",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Monitors clipboard for vocabulary words"
                setShowBadge(false)
            }

            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }
}
