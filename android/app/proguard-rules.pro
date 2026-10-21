# libsodium and JNA find their native methods by reflection, so R8 must not
# rename or remove them.
-keep class com.goterl.lazysodium.** { *; }
-keep class com.sun.jna.** { *; }
-keep class * implements com.sun.jna.** { *; }
-keepclassmembers class * extends com.sun.jna.** { *; }
-dontwarn java.awt.**
-dontwarn javax.swing.**

# Our notification listener is created by the system, never by our own code.
-keep class ca.brcook.otprelay.NotificationListener { *; }