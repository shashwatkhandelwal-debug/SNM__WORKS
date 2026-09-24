package com.snmworks.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.jan.supabase.auth.auth
import io.github.jan.supabase.auth.providers.builtin.Email
import kotlinx.coroutines.launch

// SNM Brand Palette
val SnmOlive = Color(0xFF474B2F)
val SnmDark = Color(0xFF1B2017)
val SnmGreige = Color(0xFFE9E5DA)
val SnmPaper = Color(0xFFF6F4EE)
val SnmPass = Color(0xFF3F6B34)
val SnmFail = Color(0xFFA82914)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = SnmPaper
                ) { innerPadding ->
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .padding(innerPadding)
                    ) {
                        AppRoot()
                    }
                }
            }
        }
    }
}

enum class MobileScreen {
    SESSION,
    JOBS_LIST,
    INSPECTION_PLAN,
    QC_FORM,
    QC_RESULT,
    QC_HISTORY,
    DOWNTIME_FORM,
    DOWNTIME_RESULT,
    DOWNTIME_HISTORY,
    MATERIALS_FORM,
    MATERIALS_RESULT,
    MATERIALS_HISTORY
}

@Composable
fun AppRoot() {
    var loggedInEmail by remember { mutableStateOf<String?>(null) }
    var currentScreen by remember { mutableStateOf(MobileScreen.SESSION) }
    var lastQCResult by remember { mutableStateOf<QCCheckResponse?>(null) }
    var lastDowntimeResult by remember { mutableStateOf<DowntimeLogResponse?>(null) }
    var lastMaterialsResult by remember { mutableStateOf<MaterialIssueResponse?>(null) }
    var selectedJobForPlan by remember { mutableStateOf<JobItem?>(null) }

    // Pre-fill fields for QC Form
    var prefilledJobId by remember { mutableStateOf<String?>(null) }
    var prefilledParameter by remember { mutableStateOf<String?>(null) }
    var prefilledSpecValue by remember { mutableStateOf<String?>(null) }
    var prefilledUnit by remember { mutableStateOf<String?>(null) }
    var prefilledLimitType by remember { mutableStateOf<String?>(null) }
    var prefilledStage by remember { mutableStateOf<String?>(null) }

    val scope = rememberCoroutineScope()

    fun clearQCPrefills() {
        prefilledJobId = null
        prefilledParameter = null
        prefilledSpecValue = null
        prefilledUnit = null
        prefilledLimitType = null
        prefilledStage = null
    }

    LaunchedEffect(Unit) {
        val user = SupabaseClient.client.auth.currentUserOrNull()
        if (user != null) {
            loggedInEmail = user.email
        }
    }

    if (loggedInEmail == null) {
        LoginScreen(
            onLoginSuccess = { email ->
                loggedInEmail = email
                currentScreen = MobileScreen.SESSION
            }
        )
    } else {
        when (currentScreen) {
            MobileScreen.SESSION -> {
                SessionScreen(
                    userEmail = loggedInEmail ?: "",
                    onViewJobs = {
                        currentScreen = MobileScreen.JOBS_LIST
                    },
                    onNewQCCheck = {
                        clearQCPrefills()
                        currentScreen = MobileScreen.QC_FORM
                    },
                    onViewQCHistory = {
                        currentScreen = MobileScreen.QC_HISTORY
                    },
                    onLogDowntime = {
                        currentScreen = MobileScreen.DOWNTIME_FORM
                    },
                    onViewDowntimeHistory = {
                        currentScreen = MobileScreen.DOWNTIME_HISTORY
                    },
                    onIssueMaterial = {
                        currentScreen = MobileScreen.MATERIALS_FORM
                    },
                    onViewMaterialsHistory = {
                        currentScreen = MobileScreen.MATERIALS_HISTORY
                    },
                    onSignOut = {
                        scope.launch {
                            try {
                                SupabaseClient.client.auth.signOut()
                            } catch (_: Exception) {}
                            loggedInEmail = null
                            currentScreen = MobileScreen.SESSION
                        }
                    }
                )
            }
            MobileScreen.JOBS_LIST -> {
                JobsScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    },
                    onSelectJobForPlan = { job ->
                        selectedJobForPlan = job
                        currentScreen = MobileScreen.INSPECTION_PLAN
                    }
                )
            }
            MobileScreen.INSPECTION_PLAN -> {
                val job = selectedJobForPlan
                if (job != null) {
                    InspectionPlanScreen(
                        job = job,
                        onBack = {
                            currentScreen = MobileScreen.JOBS_LIST
                        },
                        onRecordCheckForRequirement = { jobItem, reqItem ->
                            prefilledJobId = jobItem.id
                            prefilledParameter = reqItem.parameter
                            prefilledSpecValue = reqItem.specValue?.toString()
                            prefilledUnit = reqItem.unit
                            prefilledLimitType = reqItem.limitType
                            prefilledStage = reqItem.stage
                            currentScreen = MobileScreen.QC_FORM
                        }
                    )
                } else {
                    currentScreen = MobileScreen.JOBS_LIST
                }
            }
            MobileScreen.QC_FORM -> {
                QCCheckFormScreen(
                    onBack = {
                        clearQCPrefills()
                        currentScreen = MobileScreen.SESSION
                    },
                    onSubmitSuccess = { result ->
                        lastQCResult = result
                        clearQCPrefills()
                        currentScreen = MobileScreen.QC_RESULT
                    },
                    prefilledJobId = prefilledJobId,
                    prefilledParameter = prefilledParameter,
                    prefilledSpecValue = prefilledSpecValue,
                    prefilledUnit = prefilledUnit,
                    prefilledLimitType = prefilledLimitType,
                    prefilledStage = prefilledStage
                )
            }
            MobileScreen.QC_RESULT -> {
                val res = lastQCResult
                if (res != null) {
                    QCResultScreen(
                        result = res,
                        onNewCheck = {
                            clearQCPrefills()
                            currentScreen = MobileScreen.QC_FORM
                        },
                        onViewHistory = {
                            currentScreen = MobileScreen.QC_HISTORY
                        },
                        onDone = {
                            currentScreen = MobileScreen.SESSION
                        }
                    )
                } else {
                    currentScreen = MobileScreen.SESSION
                }
            }
            MobileScreen.QC_HISTORY -> {
                QCHistoryScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    }
                )
            }
            MobileScreen.DOWNTIME_FORM -> {
                DowntimeFormScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    },
                    onSubmitSuccess = { result ->
                        lastDowntimeResult = result
                        currentScreen = MobileScreen.DOWNTIME_RESULT
                    }
                )
            }
            MobileScreen.DOWNTIME_RESULT -> {
                val res = lastDowntimeResult
                if (res != null) {
                    DowntimeResultScreen(
                        result = res,
                        onNewLog = {
                            currentScreen = MobileScreen.DOWNTIME_FORM
                        },
                        onViewHistory = {
                            currentScreen = MobileScreen.DOWNTIME_HISTORY
                        },
                        onDone = {
                            currentScreen = MobileScreen.SESSION
                        }
                    )
                } else {
                    currentScreen = MobileScreen.SESSION
                }
            }
            MobileScreen.DOWNTIME_HISTORY -> {
                DowntimeHistoryScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    },
                    onLogNew = {
                        currentScreen = MobileScreen.DOWNTIME_FORM
                    }
                )
            }
            MobileScreen.MATERIALS_FORM -> {
                MaterialsFormScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    },
                    onSubmitSuccess = { result ->
                        lastMaterialsResult = result
                        currentScreen = MobileScreen.MATERIALS_RESULT
                    }
                )
            }
            MobileScreen.MATERIALS_RESULT -> {
                val res = lastMaterialsResult
                if (res != null) {
                    MaterialsResultScreen(
                        result = res,
                        onNewIssue = {
                            currentScreen = MobileScreen.MATERIALS_FORM
                        },
                        onViewHistory = {
                            currentScreen = MobileScreen.MATERIALS_HISTORY
                        },
                        onDone = {
                            currentScreen = MobileScreen.SESSION
                        }
                    )
                } else {
                    currentScreen = MobileScreen.SESSION
                }
            }
            MobileScreen.MATERIALS_HISTORY -> {
                MaterialsHistoryScreen(
                    onBack = {
                        currentScreen = MobileScreen.SESSION
                    },
                    onIssueNew = {
                        currentScreen = MobileScreen.MATERIALS_FORM
                    }
                )
            }
        }
    }
}

@Composable
fun LoginScreen(
    onLoginSuccess: (String) -> Unit
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var isLoading by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "SNM WORKS",
            fontSize = 32.sp,
            fontWeight = FontWeight.Bold,
            color = SnmDark,
            letterSpacing = 1.sp
        )
        Text(
            text = "Swadeshi Niwar Mills • Mobile",
            fontSize = 14.sp,
            color = Color.Gray
        )

        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = email,
            onValueChange = {
                email = it
                errorMessage = null
            },
            label = { Text("Email") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth(),
            enabled = !isLoading
        )

        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = password,
            onValueChange = {
                password = it
                errorMessage = null
            },
            label = { Text("Password") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
            enabled = !isLoading
        )

        if (errorMessage != null) {
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = errorMessage ?: "",
                color = SnmFail,
                fontSize = 13.sp,
                modifier = Modifier.fillMaxWidth()
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        Button(
            onClick = {
                if (email.isBlank() || password.isBlank()) {
                    errorMessage = "Please enter both email and password"
                    return@Button
                }
                isLoading = true
                errorMessage = null
                scope.launch {
                    try {
                        SupabaseClient.client.auth.signInWith(Email) {
                            this.email = email.trim()
                            this.password = password
                        }
                        val user = SupabaseClient.client.auth.currentUserOrNull()
                        onLoginSuccess(user?.email ?: email.trim())
                    } catch (e: Exception) {
                        errorMessage = e.message ?: "Sign-in failed. Please check your credentials."
                    } finally {
                        isLoading = false
                    }
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(50.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive),
            enabled = !isLoading
        ) {
            if (isLoading) {
                CircularProgressIndicator(
                    color = Color.White,
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp
                )
            } else {
                Text(
                    text = "Sign In",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }
    }
}

@Composable
fun SessionScreen(
    userEmail: String,
    onViewJobs: () -> Unit,
    onNewQCCheck: () -> Unit,
    onViewQCHistory: () -> Unit,
    onLogDowntime: () -> Unit,
    onViewDowntimeHistory: () -> Unit,
    onIssueMaterial: () -> Unit,
    onViewMaterialsHistory: () -> Unit,
    onSignOut: () -> Unit
) {
    val scrollState = rememberScrollState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Spacer(modifier = Modifier.height(8.dp))

        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = SnmGreige)
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "SESSION ACTIVE",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = SnmPass,
                    letterSpacing = 1.sp
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = "Signed in as:",
                    fontSize = 13.sp,
                    color = Color.Gray
                )
                Spacer(modifier = Modifier.height(2.dp))
                Text(
                    text = userEmail,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = SnmDark,
                    fontFamily = FontFamily.Monospace
                )
            }
        }

        Spacer(modifier = Modifier.height(20.dp))

        // 1. Jobs & Inspection Plans
        Button(
            onClick = onViewJobs,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF2C3E2D))
        ) {
            Text(
                text = "Active Jobs & Inspection Plans",
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold
            )
        }

        Spacer(modifier = Modifier.height(12.dp))

        // 2. QC Section (Check + History)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = onNewQCCheck,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = SnmOlive)
            ) {
                Text(
                    text = "New QC",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
            OutlinedButton(
                onClick = onViewQCHistory,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text(
                    text = "QC History",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = SnmDark
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // 3. Downtime Section (Log + History)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = onLogDowntime,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = SnmDark)
            ) {
                Text(
                    text = "Log Stoppage",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
            OutlinedButton(
                onClick = onViewDowntimeHistory,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text(
                    text = "Downtime Logs",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = SnmDark
                )
            }
        }

        Spacer(modifier = Modifier.height(12.dp))

        // 4. Materials Section (Issue + History)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = onIssueMaterial,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF5A6038))
            ) {
                Text(
                    text = "Issue Material",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
            OutlinedButton(
                onClick = onViewMaterialsHistory,
                modifier = Modifier
                    .weight(1f)
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text(
                    text = "Issues Log",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = SnmDark
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))

        // Sign Out
        OutlinedButton(
            onClick = onSignOut,
            modifier = Modifier
                .fillMaxWidth()
                .height(46.dp),
            shape = RoundedCornerShape(8.dp)
        ) {
            Text(
                text = "Sign Out",
                color = SnmFail,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium
            )
        }

        Spacer(modifier = Modifier.height(16.dp))
    }
}
