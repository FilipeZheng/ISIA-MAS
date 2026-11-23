
!start.

+!start <-
    .print("Volunteer BDI plans loaded").

// An emergency has been reported.
+incident(X,Y,D) : on_duty & trained <-
    !offer_assistance(X,Y,D).

+!offer_assistance(X,Y,D) <-
    .offer_help(X,Y,D).

// The operator selects this volunteer.
+assigned(X,Y) : on_duty & trained <-
    !assist_patient(X,Y).

+!assist_patient(X,Y) <-
    .commit_response(X,Y).

// The volunteer confirms a collapsed patient.
+patient_confirmed(X,Y) <-
    .print("BDI belief: patient confirmed at ", X, ", ", Y).
