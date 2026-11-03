use super::{
    error::{Error, Result},
    protocol::{checked, unpack, MAX_RESPONSE},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    io::Read,
    net::{TcpStream, ToSocketAddrs},
    sync::Arc,
    time::Duration,
};
use tungstenite::{client::IntoClientRequest, stream::MaybeTlsStream, Message, WebSocket};
use url::Url;
const PREFIX: &str = "/api/online/v2";

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Server {
    pub origin: String,
    pub ca_pem: String,
}
impl Server {
    pub fn validate(&self) -> Result<Url> {
        let value = Url::parse(&self.origin).map_err(|_| Error::new("INVALID_SERVER"))?;
        if value.scheme() != "https"
            || self.origin != value.origin().ascii_serialization()
            || !value.username().is_empty()
            || value.password().is_some()
            || value.query().is_some()
            || value.fragment().is_some()
            || !self.origin.is_ascii()
            || self.origin.len() > 1024
            || self.ca_pem.len() > 32768
            || self.ca_pem.contains("PRIVATE KEY")
        {
            return Err(Error::new("INVALID_SERVER"));
        }
        if !self.ca_pem.is_empty() {
            let certs = reqwest::Certificate::from_pem_bundle(self.ca_pem.as_bytes())
                .map_err(|_| Error::new("INVALID_TRUST_ROOT"))?;
            if certs.is_empty() {
                return Err(Error::new("INVALID_TRUST_ROOT"));
            }
        }
        Ok(value)
    }
    pub fn key(&self) -> String {
        let trust = if self.ca_pem.is_empty() {
            "system".into()
        } else {
            crate::catalog::hash(self.ca_pem.as_bytes())
        };
        crate::catalog::hash(format!("{}\n{}", self.origin, trust).as_bytes())
    }
    pub fn id(&self) -> String {
        format!("p{}", &self.key()[..24])
    }
}
pub trait Stream: Send {
    fn read(&mut self) -> Result<Option<Value>>;
    fn close(&mut self);
}
pub trait Transport: Send + Sync {
    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        token: Option<&str>,
        controller: Option<&str>,
    ) -> Result<Value>;
    fn stream(&self, token: &str, subscription: &Value) -> Result<Box<dyn Stream>>;
}
pub struct Http {
    server: Server,
    client: reqwest::blocking::Client,
    tls: Arc<rustls::ClientConfig>,
}
impl Http {
    pub fn new(server: Server) -> Result<Self> {
        server.validate()?;
        let tls = crate::tls::client(&server.ca_pem).map_err(|_| Error::new("TLS_FAILED"))?;
        let builder = reqwest::blocking::Client::builder()
            .use_preconfigured_tls(tls.clone())
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .timeout(Duration::from_secs(12))
            .connect_timeout(Duration::from_secs(6));
        Ok(Self {
            server,
            client: builder.build().map_err(|_| Error::new("TLS_FAILED"))?,
            tls: Arc::new(tls),
        })
    }
}
impl Transport for Http {
    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        token: Option<&str>,
        controller: Option<&str>,
    ) -> Result<Value> {
        if !path.starts_with('/')
            || !path
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'/' || b == b'_' || b == b'-')
            || !matches!(method, "GET" | "POST")
        {
            return Err(Error::new("INVALID_ROUTE"));
        }
        let method = reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|_| Error::new("INVALID_ROUTE"))?;
        let mut request = self
            .client
            .request(method, format!("{}{PREFIX}{path}", self.server.origin))
            .header("Accept", "application/json")
            .header("Accept-Encoding", "identity");
        if let Some(token) = token {
            request = request.bearer_auth(token);
        }
        if let Some(controller) = controller {
            request = request.header("X-PTCG-Controller", controller);
        }
        if let Some(body) = body {
            let raw = serde_json::to_vec(body)?;
            if raw.len() > 16384 {
                return Err(Error::new("PAYLOAD_TOO_LARGE"));
            }
            request = request.header("Content-Type", "application/json").body(raw);
        }
        let response = request.send().map_err(|_| Error::network())?;
        let status = response.status().as_u16();
        if (300..400).contains(&status) {
            return Err(Error::new("REDIRECT_REFUSED"));
        }
        if response
            .headers()
            .get("Content-Encoding")
            .is_some_and(|h| h != "identity")
            || response
                .headers()
                .get("Content-Type")
                .and_then(|v| v.to_str().ok())
                .and_then(|s| s.split(';').next())
                != Some("application/json")
        {
            return Err(Error::invalid());
        }
        if response
            .content_length()
            .is_some_and(|n| n > MAX_RESPONSE as u64)
        {
            return Err(Error::new("RESPONSE_TOO_LARGE"));
        }
        let mut raw = Vec::new();
        response
            .take(MAX_RESPONSE as u64 + 1)
            .read_to_end(&mut raw)
            .map_err(|_| Error::network())?;
        let value = unpack(&raw)?;
        if !(200..300).contains(&status) {
            let value = checked("ProtocolError", value)?;
            return Err(Error {
                code: value["code"].as_str().unwrap().into(),
                status,
                retryable: value["retryable"].as_bool().unwrap(),
            });
        }
        Ok(value)
    }
    fn stream(&self, token: &str, subscription: &Value) -> Result<Box<dyn Stream>> {
        let url = self.server.validate()?;
        let host = url
            .host_str()
            .unwrap()
            .trim_start_matches('[')
            .trim_end_matches(']');
        let port = url.port_or_known_default().unwrap();
        let addresses = (host, port)
            .to_socket_addrs()
            .map_err(|_| Error::network())?;
        let mut tcp = None;
        for address in addresses.take(2) {
            if let Ok(stream) = TcpStream::connect_timeout(&address, Duration::from_secs(4)) {
                tcp = Some(stream);
                break;
            }
        }
        let tcp = tcp.ok_or_else(Error::network)?;
        tcp.set_read_timeout(Some(Duration::from_secs(12)))?;
        tcp.set_write_timeout(Some(Duration::from_secs(6)))?;
        let uri = format!(
            "wss://{}{PREFIX}/stream",
            url[url::Position::BeforeHost..url::Position::AfterPort].to_owned()
        );
        let mut request = uri
            .into_client_request()
            .map_err(|_| Error::new("INVALID_SERVER"))?;
        request.headers_mut().insert(
            "Authorization",
            format!("Bearer {token}")
                .parse()
                .map_err(|_| Error::invalid())?,
        );
        let config = tungstenite::protocol::WebSocketConfig::default()
            .max_message_size(Some(MAX_RESPONSE))
            .max_frame_size(Some(MAX_RESPONSE));
        let (mut socket, _) = tungstenite::client_tls_with_config(
            request,
            tcp,
            Some(config),
            Some(tungstenite::Connector::Rustls(self.tls.clone())),
        )
        .map_err(|e| match e {
            tungstenite::HandshakeError::Failure(tungstenite::Error::Http(response)) => Error {
                code: "STREAM_REJECTED".into(),
                status: response.status().as_u16(),
                retryable: matches!(response.status().as_u16(), 429 | 503),
            },
            _ => Error::network(),
        })?;
        socket
            .send(Message::Text(subscription.to_string().into()))
            .map_err(|_| Error::network())?;
        if let MaybeTlsStream::Rustls(stream) = socket.get_mut() {
            stream.sock.set_read_timeout(Some(Duration::from_secs(1)))?;
        }
        Ok(Box::new(Socket(socket)))
    }
}
struct Socket(WebSocket<MaybeTlsStream<TcpStream>>);
impl Stream for Socket {
    fn read(&mut self) -> Result<Option<Value>> {
        match self.0.read() {
            Ok(Message::Text(text)) => unpack(text.as_bytes()).map(Some),
            Ok(Message::Binary(raw)) => unpack(&raw).map(Some),
            Ok(Message::Ping(_)) | Ok(Message::Pong(_)) => {
                self.0.flush().map_err(|_| Error::network())?;
                Ok(None)
            }
            Err(tungstenite::Error::Io(error))
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
                ) =>
            {
                Ok(None)
            }
            _ => Err(Error::network()),
        }
    }
    fn close(&mut self) {
        let _ = self.0.close(None);
    }
}
