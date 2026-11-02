package com.smartvocab.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var flaskThread: Thread? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        startFlaskServer()

        webView = WebView(this).apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = true
                allowContentAccess = true
            }

            addJavascriptInterface(ClipboardBridge(this@MainActivity), "AndroidClipboard")

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                    return false
                }
            }

            loadUrl("http://127.0.0.1:5000")
        }

        setContentView(webView)

        if (Build.VERSION.SDK_INT >= 33) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this,
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
        }

        startClipboardService()
    }

    private fun startFlaskServer() {
        flaskThread = Thread {
            try {
                val py = Python.getInstance()
                val mainModule = py.getModule("main")
                mainModule.callAttr("start_server")
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }.apply { 
            isDaemon = true
            start() 
        }
    }

    private fun startClipboardService() {
        val intent = Intent(this, ClipboardMonitorService::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        flaskThread?.interrupt()
    }
}

class ClipboardBridge(private val activity: MainActivity) {

    @JavascriptInterface
    fun getClipboardText(): String {
        val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
        val clip = clipboard.primaryClip
        return clip?.getItemAt(0)?.text?.toString() ?: ""
    }

    @JavascriptInterface
    fun addToQueueFromClipboard() {
        val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
        val clip = clipboard.primaryClip
        val text = clip?.getItemAt(0)?.text?.toString() ?: return

        Thread {
            try {
                val py = Python.getInstance()
                val mainModule = py.getModule("main")
                mainModule.callAttr("add_words_from_clipboard", text)
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }.start()
    }

    @JavascriptInterface
    fun showToast(message: String) {
        activity.runOnUiThread {
            android.widget.Toast.makeText(activity, message, android.widget.Toast.LENGTH_SHORT).show()
        }
    }
}
