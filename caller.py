from flask import Flask, request, jsonify, render_template
import requests
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client
import os
from dotenv import load_dotenv
import uuid
import logging
from supabase import create_client
import datetime
import re

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.debug = True
app.config['PROPAGATE_EXCEPTIONS'] = True

# Configuration
app.config['TWILIO_ACCOUNT_SID'] = os.getenv('TWILIO_ACCOUNT_SID')
app.config['TWILIO_AUTH_TOKEN'] = os.getenv('TWILIO_AUTH_TOKEN')
app.config['TWILIO_PHONE_NUMBER'] = os.getenv('TWILIO_PHONE_NUMBER')
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')

# Gemini API key
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# In-memory conversation store
conversations = {}

# Ngrok URL - to be pasted in the CLI
NGROK_URL = input("Paste your ngrok HTTPS URL (e.g. https://1234.ngrok.io): ").strip()

# SUPABASE
url: str = "https://bfsncbhshxroemiytzbk.supabase.co"
key: str = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJmc25jYmhzaHhyb2VtaXl0emJrIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTcyNDY3OTI1MywiZXhwIjoyMDQwMjU1MjUzfQ.58LjVm27XFpJllDf0RKBt5CisJq81SD_4PWYa8CxZOY"
supabase = create_client(url, key)

def get_active_conversation_id(job_id, applicant_email):
    # Query Supabase for an active conversation for this job/applicant
    result = supabase.table("conversations") \
        .select("conversation_id,status") \
        .eq("job_id", job_id) \
        .eq("applicant_email", applicant_email) \
        .neq("status", "completed") \
        .execute()
    data = result.data
    if data and len(data) > 0:
        return data[0]["conversation_id"]
    return None

def call_gemini_ai(prompt):
    logger.info("Sending Gemini Prompt: " + prompt)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        if 'candidates' in data and data['candidates']:
            parts = data['candidates'][0].get('content', {}).get('parts', [])
            if parts:
                toReturn = parts[0].get('text', '...')
                logger.info("Gemini Response: %s", str(toReturn))
                return str(toReturn)
        return "I'm having trouble understanding. Could you repeat that?"
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "Something went wrong. Could you please repeat?"

def generate_ai_response(conversation_history, user_input, context):
    system_message = f'''
You are an AI interview for a job position. Your job is to determine the candidate's fit. Remember, TIME IS MONEY. Ask the MINIMUM number of questions needed.
If at any point you need to ask a follow up, always start your QUESTION_TEXT with something along the lines of "For clarification...".

Instructions:
- Introduce yourself as Otto and explain your job as an interviewer designed to assess fit.
- Then, inform applicant that you will ask questions about their resume.
- Ask ONE clarifying question about the applicant's resume.
- TIME IS MONEY: Do NOT ask a clarifying question about the candidate's response unless ABSOLUTELY NECESSARY.
- After you finish the clarifying resume question, tell applicant you will assess their abilities.
- Ask ONE technical question related to the job position. When you ask the technical question, VERY CLEARLY SAY "Now, I will ask you a technical question related to the job position."
- TIME IS MONEY: Do NOT ask a clarifying question about the candidate's response unless ABSOLUTELY NECESSARY.
- Once that is finished, thank the applicant. set QUESTION_TYPE to END.

You will be provided the previous conversation history in subsequent prompts. **ONLY ASK ONE QUESTION AT A TIME. YOUR RESPONSE WILL BE THE QUESTION.**

RESUME: "{context.get('resume')}"
JOB DESCRIPTION: "{context.get('issue_description')}"

Here is the conversation history. USE IT TO YOUR ADVANTAGE to determine whether it's time to ask a clarifying question or move on to a technical question: {conversation_history}

You response must ALWAYS be in the following format. DO NOT BREAK THIS PATTERN WHATSOEVER.
QUESTION_TYPE,QUESTION_TEXT
QUESTION_TYPE is either the value RESUME or TECHNICAL depending on the type of question you are asking (if you are asking a follow up to a RESUME question, the QUESTION_TYPE should also be RESUME), or END is the call is finished.

Final reminder: DON'T ASK CLARIFYING QUESTIONS UNLESS ABSOLUTELY NECESSARY. TIME IS MONEY.

The QUESTION_TYPE and QUESTION_TEXT must ALWAYS BE SEPARATED BY A COMMA in the above format.
'''
    conversation_text = system_message + "\n"
    for msg in conversation_history[-5:]:
        speaker = "Customer Service" if msg['role'] == "user" else "You"
        conversation_text += f"{speaker}: {msg['content']}\n"
    if user_input:
        conversation_text += f"Customer Service: {user_input}\nYou:"
    response = call_gemini_ai(conversation_text)
    return response[:500] + "..." if len(response) > 500 else response

def upsert_conversation_to_supabase(conversation_id, conversation):
    print("CONVERSATION ID:", conversation_id)
    now = datetime.datetime.utcnow().isoformat()
    context = conversation['context']
    applicant_email = context.get('email')
    if not applicant_email and 'resume' in context:
        match = re.search(r'[\w\.-]+@[\w\.-]+', context['resume'])
        if match:
            applicant_email = match.group(0)
    data = {
        "conversation_id": conversation_id,
        "applicant_email": applicant_email,
        "job_id": context.get('job_id'),
        "history": conversation['history'],
        "status": context.get('status'),
        "call_sid": context.get('call_sid'),
        "started_at": context.get('started_at', now),
        "updated_at": now,
        "score": context.get('score'),
    }
    try:
        supabase.table("conversations").upsert(data, on_conflict="conversation_id").execute()
    except Exception as e:
        logger.error(f"Supabase upsert error: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/initiate_call', methods=['POST'])
def initiate_call():
    data = request.form
    required_fields = ['user_number', 'customer_service_number', 'issue_description', 'resume']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    conversation_id = str(uuid.uuid4())
    conversations[conversation_id] = {
        'history': [],
        'context': {
            'user_number': data['user_number'],
            'customer_service_number': data['customer_service_number'],
            'issue_description': data['issue_description'],
            'resume': data['resume'],
            'status': 'initiating',
            'call_sid': None
        }
    }
    client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
    webhook_url = f"{NGROK_URL}/voice/{conversation_id}"
    status_callback_url = f"{NGROK_URL}/call_status/{conversation_id}"
    call = client.calls.create(
        url=webhook_url,
        to=data['customer_service_number'],
        from_=app.config['TWILIO_PHONE_NUMBER'],
        record=True,
        status_callback=status_callback_url,
        status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
        timeout=30
    )
    conversations[conversation_id]['context']['call_sid'] = call.sid
    conversations[conversation_id]['context']['status'] = 'initiated'
    upsert_conversation_to_supabase(conversation_id, conversations[conversation_id])
    return jsonify({
        'status': 'call_initiated',
        'conversation_id': conversation_id,
        'call_sid': call.sid
    })

def retrieveApplicantDetails(listingID):
    jobDescription = (
        supabase.table("listings").select("*").eq("job_id", listingID).execute()
    ).data[0]["description"]
    applicant = (
        supabase.table("applicants").select("*").eq("appliedTo", listingID).eq("email", "vangara.anirudhbharadwaj@gmail.com").execute()
    ).data[0]
    resume = applicant["resume"]
    phone = applicant["phone"]
    return {
        "resume": resume,
        "phone": phone,
        "jobDescription": jobDescription
    }

@app.route('/initiate_fake_call', methods=['POST'])
def initiate_fake_call():
    data = request.form
    required_fields = ['listingID']
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
    details = retrieveApplicantDetails(data['listingID'])
    conversation_id = str(uuid.uuid4())
    print("GENERATED CONVERSATION ID:", conversation_id)
    conversations[conversation_id] = {
        'history': [],
        'context': {
            "job_id": data['listingID'],
            'user_number': details['phone'],
            'customer_service_number': details['phone'],
            'issue_description': details['jobDescription'],
            'resume': details['resume'],
            'status': 'initiating',
            'call_sid': None,
            'score': None
        }
    }
    client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
    webhook_url = f"{NGROK_URL}/voice/{conversation_id}"
    status_callback_url = f"{NGROK_URL}/call_status/{conversation_id}"
    call = client.calls.create(
        url=webhook_url,
        to=details['phone'],
        from_=app.config['TWILIO_PHONE_NUMBER'],
        record=True,
        status_callback=status_callback_url,
        status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
        timeout=30
    )
    conversations[conversation_id]['context']['call_sid'] = call.sid
    conversations[conversation_id]['context']['status'] = 'initiated'
    upsert_conversation_to_supabase(conversation_id, conversations[conversation_id])
    return jsonify({
        'status': 'call_initiated',
        'conversation_id': conversation_id,
        'call_sid': call.sid
    })

@app.route('/voice/<conversation_id>', methods=['POST'])
def voice(conversation_id):
    response = VoiceResponse()
    if conversation_id not in conversations:
        response.say("Invalid conversation. Goodbye.")
        response.hangup()
        return str(response), 200
    conversation = conversations[conversation_id]
    user_input = request.form.get('SpeechResult', '').strip()
    if 'started_at' not in conversation['context']:
        conversation['context']['started_at'] = datetime.datetime.utcnow().isoformat()
    if not conversation['history'] and not user_input:
        greetingPromptTemplate = f'''
You are an AI interview for a job position. Your job is to determine the candidate's fit.

You are an AI interview for a job position. Your job is to determine the candidate's fit. Remember, TIME IS MONEY. Ask the MINIMUM number of questions needed.
If at any point you need to ask a follow up, always start your QUESTION_TEXT with something along the lines of "For clarification...".

Instructions:
- Introduce yourself as Otto and explain your job as an interviewer designed to assess fit.
- Then, inform applicant that you will ask questions about their resume.
- Ask ONE clarifying question about the applicant's resume.
- TIME IS MONEY: Do NOT ask a clarifying question about the candidate's response unless ABSOLUTELY NECESSARY.
- After you finish the clarifying resume question, tell applicant you will assess their abilities.
- Ask ONE technical question related to the job position.
- TIME IS MONEY: Do NOT ask a clarifying question about the candidate's response unless ABSOLUTELY NECESSARY.
- Once that is finished, thank the applicant. set QUESTION_TYPE to END.

Do the introduction, then ask only one question. YOUR RESPONSE WILL BE READ TO THE USER.

RESUME: "{conversation['context']['resume']}"
JOB DESCRIPTION: "{conversation['context']['issue_description']}"

You response must ALWAYS be in the following format. DO NOT BREAK THIS PATTERN WHATSOEVER.
QUESTION_TYPE,QUESTION_TEXT
QUESTION_TYPE is either the value RESUME or TECHNICAL depending on the type of question you are asking.

The QUESTION_TYPE and QUESTION_TEXT must ALWAYS BE SEPARATED BY A COMMA in the above format.
'''
        [question_type, question_text] = call_gemini_ai(greetingPromptTemplate).split(',', 1)
        conversation['history'].append({"role": "interviewer", "question_type": question_type, "content": question_text})
        response.say(question_text, voice='alice', language='te-IN')
    elif user_input:
        [question_type, question_text] = generate_ai_response(conversation['history'], user_input, conversation['context']).split(',', 1)
        
        if question_type == "END":
            response.say(question_text, voice='alice')
            response.hangup()

            scorePrompt = f'''
            This is a transcript of an interview with a candidate for a job position. Your job is to determine the candidate's fit based on the conversation history.

            The score must be a number from 1 to 10, where 1 is the worst fit and 10 is the best fit.
            The score must be based on the candidate's responses to the questions asked by the interviewer.

            Here is the job description: {conversation['context']['issue_description']}
            Here is the transcript: {conversation['history']}

            Remember, your response MUST BE A NUMBER FROM 1 to 10. NOTHING ELSE.
            '''
            score = call_gemini_ai(scorePrompt)
            conversation['context']['score'] = score
            upsert_conversation_to_supabase(conversation_id, conversation)
        else:
            conversation['history'].append({"role": "interviee", "content": user_input})
            conversation['history'].append({"role": "assistant", "question_type": question_type, "content": question_text})
            response.say(question_text, voice='alice', language='te-IN')
    else:
        response.say("I didn't catch that. Please say that again.", voice='alice')
    gather = Gather(
        input='speech',
        speech_timeout=2,
        timeout=3,
        action=f'/voice/{conversation_id}',
        method='POST',
        speech_model='experimental_conversations',
        language='en-US'
    )
    gather.say("You may speak now.", voice='alice')
    response.append(gather)
    response.say("No input detected. Goodbye.", voice='alice')
    response.hangup()
    return str(response), 200

@app.route('/call_status/<conversation_id>', methods=['POST'])
def call_status(conversation_id):
    if conversation_id in conversations:
        status = request.form.get('CallStatus')
        conversations[conversation_id]['context']['status'] = status
        upsert_conversation_to_supabase(conversation_id, conversations[conversation_id])
    return '', 200

@app.route('/get_conversation/<conversation_id>')
def get_conversation(conversation_id):
    if conversation_id in conversations:
        return jsonify({'status': 'success', 'conversation': conversations[conversation_id]})
    return jsonify({'status': 'not_found'}), 404

@app.route('/end_call/<conversation_id>', methods=['POST'])
def end_call(conversation_id):
    if conversation_id in conversations:
        try:
            client = Client(app.config['TWILIO_ACCOUNT_SID'], app.config['TWILIO_AUTH_TOKEN'])
            call_sid = conversations[conversation_id]['context']['call_sid']
            if call_sid:
                client.calls(call_sid).update(status='completed')
                conversations[conversation_id]['context']['status'] = 'ended'
                upsert_conversation_to_supabase(conversation_id, conversations[conversation_id])
                return jsonify({'status': 'call_ended'})
        except Exception as e:
            logger.error(f"End call error: {e}")
            return jsonify({'error': 'Failed to end call'}), 500
    return jsonify({'status': 'not_found'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(404)
def not_found(error):
    logger.error(f"404 error: {error}")
    return jsonify({'error': 'Not found'}), 404

if __name__ == '__main__':
    missing = [v for v in ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_PHONE_NUMBER', 'GEMINI_API_KEY'] if not os.getenv(v)]
    if missing:
        logger.error(f"Missing env variables: {missing}")
    app.run(host='0.0.0.0', port=5000, debug=True)
